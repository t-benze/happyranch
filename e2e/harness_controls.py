"""Finite harness controls, outside the retired Python suite and Product E2E.

Run with the locked browser dependencies for G3. No live daemon is contacted.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MethodType, SimpleNamespace
from typing import Callable


def refused(action: Callable, label: str) -> None:
    try:
        action()
    except (AssertionError, ValueError):
        return
    raise AssertionError("control accepted " + label)


def clock(end: float, cap: float = 30) -> float:
    remaining = end - time.monotonic()
    assert remaining > 0, "control original deadline exhausted"
    return min(cap, remaining)


def g1(root: Path) -> None:
    import jobs
    import checks
    from owned import identity

    program = '''import json, os, subprocess, sys
children = [subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.buffer.read(1)'],
            stdin=subprocess.PIPE, env=env) for env in (dict(os.environ), {})]
print(json.dumps([p.pid for p in children]), flush=True)
try:
    sys.stdin.buffer.read(1)
finally:
    for p in children:
        p.communicate(b'R', timeout=3)
'''
    with subprocess.Popen([sys.executable, "-c", program], env=dict(os.environ, E2E_ROOT=str(root)),
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as process:
        provider_pid, guardian_pid = json.loads(process.stdout.readline())
        boot = dict(pid=process.pid, start=identity(process.pid))
        provider = dict(pid=provider_pid, pid_start=identity(provider_pid))
        c = SimpleNamespace(root=root, daemon=process, boots=[boot], remaining=lambda: clock(end))
        end = time.monotonic() + 5
        try:
            frame = jobs.refusal_processes(c, provider)
            by_pid = {r['pid']: r for r in frame['rows']}
            assert set(by_pid) == {process.pid, provider_pid, guardian_pid}
            assert by_pid[guardian_pid]['root_marker'] is None
            checks.refusal_process_content(frame, boot, provider, root)
            bad = copy.deepcopy(frame)
            prefix, fields = bad['rows'][0]['stat_end'].rsplit(')', 1)
            fields = fields.split()
            fields[19] = 'wrong-generation'
            bad['rows'][0]['stat_end'] = prefix + ') ' + ' '.join(fields)
            refused(lambda: checks.refusal_process_content(bad, boot, provider, root), 'changed generation bookend')
            bad = copy.deepcopy(frame)
            for row in bad['rows']:
                row['children'] = [p for p in row['children'] if p != guardian_pid]
                row['children_end'] = list(row['children'])
            refused(lambda: checks.refusal_process_content(bad, boot, provider, root), 'unknown unmarked descendant')
            boot['start'] = 'wrong-generation'
            refused(lambda: jobs.refusal_processes(c, provider), 'reused root generation')
        finally:
            process.communicate('R', timeout=3)
        assert process.returncode == 0


def g2(root: Path) -> None:
    import jobs
    import checks
    from controller import Controller
    from owned import identity

    address = root / 'job.sock'
    sentinel = root / 'started.json'
    (root / 'owned.json').write_text('[]')
    script = f'''import json, os, socket, sys
from pathlib import Path
hello = dict(token='control', pid=os.getpid())
Path({str(sentinel)!r}).write_text(json.dumps(hello))
with socket.socket(socket.AF_UNIX) as peer:
    peer.connect({str(address)!r})
    peer.sendall((json.dumps(hello)+'\\n').encode())
    assert peer.recv(1) == b'R'
print('CONTROL_OUT', flush=True)
print('CONTROL_ERR', file=sys.stderr, flush=True)
raise SystemExit(7)
'''
    guardian = Path(__file__).resolve().parents[1] / 'runtime/daemon/job_guardian.py'
    command = [sys.executable, str(guardian), '--seconds', '15', '--stderr-path', str(root/'stderr'),
               '--job-id', 'control', sys.executable, '-c', script]
    supervisor = f'''import subprocess
p = subprocess.Popen({command!r}, start_new_session=True)
print(p.pid, flush=True)
raise SystemExit(p.wait(timeout=23))
'''
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(address))
        server.listen(1)
        with subprocess.Popen([sys.executable, '-c', supervisor], env=dict(os.environ, E2E_ROOT=str(root)),
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            guardian_pid = int(process.stdout.readline())
            guardian_start = identity(guardian_pid)
            end = time.monotonic() + 10
            boot = dict(pid=process.pid, start=identity(process.pid))
            job = dict(id='control', token='control', sentinel=str(sentinel), code=7, cap=5)
            row = lambda: dict(id='control', status='running' if identity(guardian_pid) == guardian_start else 'completed')
            c = SimpleNamespace(root=root, daemon=process, boots=[boot], durable=[], job_connections=[],
                remaining=lambda cap=30: clock(end, cap), bounded=lambda end: nullcontext(),
                stores={'alpha': SimpleNamespace(jobs=lambda task: dict(jobs=[row()]))},
                browser=SimpleNamespace(job_action=lambda *args, **kw: {}, job_open=lambda *args, **kw: {}))
            c.poll = MethodType(Controller.poll, c)
            try:
                handle = jobs.observe_running(c, 'task', job, server)
                assert job['peer']['guardian']['pid'] == guardian_pid != job['peer']['pid']
                jobs.run_script(c, 'task', job, server, handle)
                out, err = process.communicate(timeout=3)
                assert process.returncode == 7 and out == 'CONTROL_OUT\n' and err == 'CONTROL_ERR\n'
                checks.job_process_content(job['peer'], job['closure'], boot, root, 'control')
                for key, value in [('members', [dict(pid=guardian_pid)]), ('eof', False),
                                   ('observed_start', job['peer']['start'])]:
                    bad = copy.deepcopy(job['closure'])
                    bad[key] = value
                    refused(lambda: checks.job_process_content(job['peer'], bad, boot, root, 'control'), key)
                bad = copy.deepcopy(job['closure'])
                bad['guardian']['observed_start'] = guardian_start
                refused(lambda: checks.job_process_content(job['peer'], bad, boot, root, 'control'), 'surviving guardian')
                for key, value in [('root_marker', 'E2E_ROOT=another-run'), ('ppid', process.pid), ('census', [])]:
                    bad_peer = copy.deepcopy(job['peer'])
                    bad_peer[key] = value
                    refused(lambda: checks.job_process_content(bad_peer, job['closure'], boot, root, 'control'), key)
            finally:
                for connection in c.job_connections:
                    connection.close()
                if identity(guardian_pid) == guardian_start:
                    os.killpg(guardian_pid, signal.SIGKILL)
                process.communicate(timeout=3)


def g3(root: Path) -> None:
    from browser import Browser, _RENDERED_DOM
    from playwright.sync_api import sync_playwright

    href = '/orgs/alpha/tasks/TASK-1'
    fixtures = {
        'list': f'<main><a href="{href}"><div><span class="text-task-title" title="TITLE">TITLE</span></div></a></main>',
        'detail': '<div><section><h3>Brief</h3><div><div class="gl-prose"><p>BRIEF</p></div></div></section></div>',
        'recall': f'<div><section><h3>Recall tree</h3><div><div><a href="{href}">TASK-1</a><span>completed</span></div></div></section></div>',
    }
    # Real local documents; the owner expression and Playwright locator both
    # come from browser.py. Fixtures supply DOM only, never owner resolution.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'<html lang="en"><body></body></html>')

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            try:
                page = browser.new_page()
                owner = Browser.__new__(Browser)
                owner.page = page
                owner.controller = SimpleNamespace(remaining=lambda cap=30: min(cap, 0.2))
                for surface, content in fixtures.items():
                    path = '/orgs/alpha/tasks' if surface == 'list' else href
                    page.goto(f'http://127.0.0.1:{server.server_port}{path}')
                    page.set_content(f'<html lang="en"><main><div class="contents"><div>{content}</div></div></main></html>')
                    args = dict(org='alpha', task='TASK-1', surface=surface, locale='en')
                    assert owner._task_surface(surface).count() == 1, 'G3 exact routed ' + surface + ' owner'
                    assert owner._task_shell().evaluate(_RENDERED_DOM, args)['surface'] == surface
                    try:
                        owner._task_shell().evaluate(_RENDERED_DOM, dict(args, org='beta'))
                    except Exception as exc:
                        assert 'rendered-owner:url' in str(exc)
                    else:
                        raise AssertionError('wrong org accepted')
                    page.locator('div.contents').evaluate('(el) => el.hidden = true')
                    try:
                        owner._task_shell().evaluate(_RENDERED_DOM, args)
                    except Exception as exc:
                        assert 'hidden-routed' in str(exc)
                    else:
                        raise AssertionError('hidden recovery DOM accepted')
                    refused(lambda: owner._task_surface(surface), 'hidden recovery locator')
                    page.locator('div.contents').evaluate('(el) => { el.hidden = false; el.appendChild(el.firstElementChild.cloneNode(true)); }')
                    try:
                        owner._task_shell().evaluate(_RENDERED_DOM, args)
                    except Exception as exc:
                        assert 'rendered-owner:' + ('list' if surface == 'list' else 'detail') in str(exc)
                    else:
                        raise AssertionError('ambiguous page owner accepted')
                    refused(lambda: owner._task_surface(surface), 'ambiguous page locator')
            finally:
                browser.close()
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def g4(root: Path) -> None:
    from controller import Controller

    responses, requests = [], []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(('GET', self.path))
            status, body = responses.pop(0) if responses else (503, {'detail': 'census_database_busy'})
            self.send_response(status)
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def log_message(self, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    def controller() -> SimpleNamespace:
        end = time.monotonic() + 0.25
        c = SimpleNamespace(port=server.server_port, token='synthetic', http=[], phases=[],
                            remaining=lambda cap=30: clock(end, cap), bounded=lambda end: nullcontext())
        for name in ('api', 'get', 'poll', 'finish'):
            setattr(c, name, MethodType(getattr(Controller, name), c))
        return c
    try:
        busy = (503, dict(detail='census_database_busy'))
        responses[:] = [busy, (200, dict(task=dict(status='completed'))), (200, {})]
        c = controller()
        assert c.finish('alpha', 'TASK-1')['task']['status'] == 'completed'
        assert [r['status'] for r in c.http] == [503, 200, 200]
        for response in [(503, dict(detail='other')), (500, dict(detail='census_database_busy')),
                         (503, dict(detail='census_database_busy', extra=True))]:
            responses[:] = [response]
            refused(lambda: controller().finish('alpha', 'TASK-1'), 'non-exact busy response')
        responses[:] = [busy]
        refused(lambda: controller().get('/strict'), 'ordinary GET busy')
        responses[:] = [(200, dict(task=dict(status='completed'))), busy]
        refused(lambda: controller().finish('alpha', 'TASK-1'), 'Recall GET busy')
        responses.clear()
        begin = time.monotonic()
        refused(lambda: controller().finish('alpha', 'TASK-1'), 'original deadline')
        assert time.monotonic() - begin < 0.5
        assert all(method == 'GET' for method, _ in requests)
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--group', choices=['G1', 'G2', 'G3', 'G4', 'all'], default='all')
    parser.add_argument('--source', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    sys.path.insert(0, str(args.source.resolve()))
    importlib.invalidate_caches()
    assert os.environ.get('TMPDIR') and not Path(os.environ['TMPDIR']).is_relative_to('/tmp'), 'set short non-/tmp TMPDIR'
    for name, control in [('G1', g1), ('G2', g2), ('G3', g3), ('G4', g4)]:
        if args.group in {name, 'all'}:
            with tempfile.TemporaryDirectory(prefix=name+'-') as directory:
                control(Path(directory))
            print(name + ' harness controls PASS', flush=True)


if __name__ == '__main__':
    main()
