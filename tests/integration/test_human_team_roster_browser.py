"""Finite Wbrowser selection: built SPA, owned loopback API, installed browser.

Authored for the authorized disposable L/W venue only. No live-host execution,
browser download, fallback mock app, or source-authoring behavioral PASS.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit

import httpx
import pytest

from tests.integration.test_human_team_roster_e2e import human_daemon, _auth_headers, _base

pytestmark = pytest.mark.integration


@contextmanager
def _spa(dist: Path, daemon_port: int):
    """Serve the actual build; proxy only to the fixture-owned local daemon."""
    state = {'mode': 'populated', 'requests': [], 'release': threading.Event(),
             'teams_mode': 'populated', 'teams_release': threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log the synthetic bearer or private request bodies.

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def do_PUT(self):
            self.respond()

        def respond(self):
            path = urlsplit(self.path).path
            if path.startswith('/api/'):
                state['requests'].append((self.command, path))
                if path == '/api/v1/orgs/test/teams' and self.command == 'GET':
                    if state['teams_mode'] == 'loading':
                        if not state['teams_release'].wait(30):
                            self.send_error(504)
                            return
                    if state['teams_mode'] == 'error':
                        self.send_response(503)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(b'{"detail":"synthetic team roster outage"}')
                        return
                if path == '/api/v1/orgs/test/agents' and self.command == 'GET':
                    if state['mode'] == 'loading':
                        if not state['release'].wait(30):
                            self.send_error(504)
                            return
                    if state['mode'] == 'empty':
                        raw = b'{"agents":[]}'
                        self.send_response(200)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                    if state['mode'] == 'error':
                        self.send_response(503)
                        self.send_header('Content-Type', 'application/json')
                        self.end_headers()
                        self.wfile.write(b'{"detail":"synthetic roster outage"}')
                        return
                connection = http.client.HTTPConnection('127.0.0.1', daemon_port, timeout=30)
                try:
                    body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                    headers = {key: value for key, value in self.headers.items()
                               if key.lower() not in ('host', 'connection', 'content-length')}
                    connection.request(self.command, self.path, body, headers)
                    response = connection.getresponse()
                    raw = response.read()
                    if (path == '/api/v1/orgs/test/teams' and self.command == 'GET'
                            and state['mode'] == 'empty' and response.status == 200):
                        roster = json.loads(raw)
                        for row in roster['teams']:
                            if row['name'] == 'default':
                                assert row['manager'] is None and row['manager_kind'] == 'human'
                                row['workers'] = []
                        raw = json.dumps(roster).encode()
                    self.send_response(response.status)
                    self.send_header('Content-Type', response.getheader('Content-Type', 'application/json'))
                    self.end_headers()
                    self.wfile.write(raw)
                finally:
                    connection.close()
                return
            relative = Path(unquote(path).lstrip('/'))
            target = dist / relative
            if '..' in relative.parts or not target.resolve().is_relative_to(dist):
                self.send_error(404)
                return
            if not target.is_file():
                target = dist / 'index.html'
            self.send_response(200)
            self.send_header('Content-Type', {'.js': 'text/javascript', '.css': 'text/css',
                '.svg': 'image/svg+xml', '.html': 'text/html'}.get(target.suffix, 'application/octet-stream'))
            self.end_headers()
            self.wfile.write(target.read_bytes())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, name='C10-owned-loopback')
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', state
    finally:
        state['release'].set()
        state['teams_release'].set()
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()


def _result(output: str):
    raw = output.rsplit('### Result', 1)[-1].split('\n###', 1)[0].strip()
    value = json.loads(raw)
    return json.loads(value) if isinstance(value, str) else value


@pytest.mark.parametrize('locale', ['en', 'zh-CN'])
@pytest.mark.parametrize('viewport', [(390, 844), (1440, 900)], ids=['390x844', '1440x900'])
def test_c10_bilingual_existing_views(human_daemon: tuple[int, Path], tmp_path: Path,
                                      locale: str, viewport: tuple[int, int]) -> None:
    from tests.helpers.integration_stub_guard.guard import manifest, require_parent_environment
    require_parent_environment()
    binding = manifest()
    source = Path(binding['source'])
    dist = source / 'web/dist'
    assert (dist / 'index.html').is_file(), 'existing supported Web build required'
    cli = shutil.which('playwright-cli')
    assert cli is not None, 'existing authorized disposable browser/CLI capability required; no download/fallback'
    cli_version = subprocess.run([cli, '--version'], check=True, text=True, capture_output=True, timeout=15).stdout.strip()
    port, root = human_daemon
    assert root.is_relative_to(Path(binding['root'])), 'browser may address only the parent-owned fixture'
    session = 'c10-' + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16]
    evidence = []

    def pw(*arguments: str) -> str:
        actual = subprocess.run([cli, '-s=' + session, *arguments], text=True, capture_output=True, timeout=40)
        assert actual.returncode == 0, (arguments[0], actual.returncode, actual.stderr)
        return actual.stdout

    def evaluate(expression: str):
        return _result(pw('eval', 'async () => JSON.stringify(await (' + expression + '))'))

    def wait(expression: str):
        deadline = time.monotonic() + 20
        actual = None
        while time.monotonic() < deadline:
            actual = evaluate(expression)
            if actual:
                return actual
            time.sleep(0.1)
        raise AssertionError((expression, actual))

    def shot(view: str, state: str):
        target = tmp_path / f'C10-{locale}-{viewport[0]}x{viewport[1]}-{view}-{state}.png'
        pw('screenshot', '--filename=' + str(target))
        assert target.is_file() and target.stat().st_size > 0
        evidence.append({'view': view, 'state': state, 'screenshot': str(target),
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'geometry': evaluate('({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth})')})
        assert evidence[-1]['geometry']['width'] == viewport[0]
        assert evidence[-1]['geometry']['height'] == viewport[1]
        assert evidence[-1]['geometry']['documentWidth'] <= viewport[0]

    labels = {'en': ('Managed by Founder', 'Language', 'Model', 'Save agent', 'Retry', 'Could not load agents.', 'No agents enrolled'),
              'zh-CN': ('由创始人管理', '语言', '模型', '保存智能体', '重试', '无法加载智能体。', '尚未登记智能体')}
    founder, language, model, save, retry, error, empty = labels[locale]
    selector = 'input[aria-label=' + json.dumps(model) + ']'
    with _spa(dist, port) as (base, fixture):
        pw('open')
        try:
            pw('resize', str(viewport[0]), str(viewport[1]))
            pw('goto', base + '/orgs/test/agents/consultant_head')
            pw('localstorage-set', 'happyranch.ui.locale', locale)
            pw('reload')
            wait(f'document.documentElement.lang==={json.dumps(locale)} && Boolean(document.querySelector({json.dumps(selector)}))')
            assert evaluate(f'document.body.innerText.includes({json.dumps(founder)})')
            assert not evaluate('Boolean(document.querySelector("[data-testid=team-escalation-policy]"))')
            assert not any('team-escalation-policy' in path for _, path in fixture['requests'])
            shot('agents', 'populated-head')
            # Actual browser edits, not a test-side replacement editor. Keep
            # node identity/draft/selection through the real locale selector.
            evaluate(f'''(() => {{const input=document.querySelector({json.dumps(selector)}); window.__c10Model=input;
                Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,'c10-worker-draft');
                input.dispatchEvent(new Event('input',{{bubbles:true}})); input.focus(); input.setSelectionRange(2,6); return true;}})()''')
            next_locale = 'zh-CN' if locale == 'en' else 'en'
            next_language, next_save = labels[next_locale][1], labels[next_locale][3]
            evaluate('document.querySelector(' + json.dumps('[aria-label=' + json.dumps(language) + ']') + ').click()')
            wait('Boolean(document.querySelector("[role=option]"))')
            evaluate(f'Array.from(document.querySelectorAll("[role=option]")).find(e=>e.querySelector("[lang={next_locale}]")).click()')
            wait(f'document.documentElement.lang==={json.dumps(next_locale)}')
            retained = evaluate('({same:window.__c10Model.isConnected,value:window.__c10Model.value,start:window.__c10Model.selectionStart,end:window.__c10Model.selectionEnd,policy:Boolean(document.querySelector("[data-testid=team-escalation-policy]"))})')
            assert retained == {'same': True, 'value': 'c10-worker-draft', 'start': 2, 'end': 6, 'policy': False}
            assert not any(method != 'GET' for method, _ in fixture['requests'])
            wait('Boolean(document.querySelector(' + json.dumps('[aria-label=' + json.dumps(next_language) + ']') + '))')
            evaluate(f'Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==={json.dumps(next_save)}).click()')
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                agents = httpx.get(_base(port) + '/agents', headers=_auth_headers()).raise_for_status().json()['agents']
                if next(row for row in agents if row['name'] == 'consultant_head')['model'] == 'c10-worker-draft':
                    break
                time.sleep(0.1)
            else:
                raise AssertionError('original Save did not persist the exact worker draft')
            assert sum(method == 'PUT' and path.endswith('/consultant_head/model') for method, path in fixture['requests']) == 1
            # State screenshots stay in the selected parameter's language.
            pw('localstorage-set', 'happyranch.ui.locale', locale)
            pw('goto', base + '/orgs/test/agents/consultant_codex')
            wait(f'location.pathname.endsWith("consultant_codex") && document.documentElement.lang==={json.dumps(locale)} && Boolean(document.querySelector({json.dumps(selector)}))')
            assert evaluate(f'document.body.innerText.includes({json.dumps(founder)})')
            shot('agents', 'populated-codex')
            for mode in ('loading', 'error', 'empty'):
                fixture['mode'] = mode
                fixture['release'].clear()
                pw('goto', base + '/orgs/test/agents')
                if mode == 'loading':
                    wait('Boolean(document.querySelector("[role=status].animate-pulse"))')
                else:
                    wait(f'document.body.innerText.includes({json.dumps(error if mode == "error" else empty)})')
                shot('agents', mode)
                if mode == 'empty':
                    cta = 'Add agent' if locale == 'en' else '添加智能体'
                    evaluate(f'Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==={json.dumps(cta)}).click()')
                    wait('Boolean(document.querySelector("[role=dialog] #agent-team"))')
                    option = evaluate('Array.from(document.querySelectorAll("#agent-team option")).find(e=>e.value==="default").textContent')
                    assert 'default' in option and founder in option
                    assert not evaluate('Array.from(document.querySelectorAll("#agent-team option")).some(e=>e.value==="founder")')
                    evaluate('document.querySelector("#agent-team").focus()')
                    assert evaluate('document.activeElement.id==="agent-team"')
                    shot('add-agent', 'empty-human-default')
                    close = 'Close' if locale == 'en' else '关闭'
                    evaluate('document.querySelector(' + json.dumps('[role=dialog] button[aria-label=' + json.dumps(close) + ']') + ').click()')
                    wait('!document.querySelector("[role=dialog]")')
                if mode == 'error':
                    fixture['mode'] = 'populated'
                    evaluate(f'Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==={json.dumps(retry)}).click()')
                    wait('document.body.innerText.includes("consultant_codex")')
                    shot('agents', 'recovered')
                fixture['release'].set()
            fixture['mode'] = 'populated'
            fixture['teams_mode'] = 'loading'
            fixture['teams_release'].clear()
            pw('goto', base + '/orgs/test/agents')
            wait('document.body.innerText.includes("consultant_head")')
            cta = 'Add agent' if locale == 'en' else '添加智能体'
            create_label = 'Create' if locale == 'en' else '创建'
            team_loading = 'Loading teams…' if locale == 'en' else '正在加载团队…'
            team_error = 'Could not load teams.' if locale == 'en' else '无法加载团队。'
            evaluate(f'Array.from(document.querySelectorAll("button")).find(e=>e.textContent.trim()==={json.dumps(cta)}).click()')
            wait(f'Boolean(document.querySelector("[role=dialog] [role=status]")) && document.body.innerText.includes({json.dumps(team_loading)})')
            enrollment = {'name': 'browser_worker', 'description': 'Browser worker draft',
                          'system_prompt': 'Preserved browser prompt'}
            # Edit the existing controls. Source fixture changes only HTTP
            # availability, never component state or the enrollment result.
            evaluate(f'''(() => {{for (const [id,value] of Object.entries({json.dumps({
                'agent-name': enrollment['name'], 'agent-description': enrollment['description'],
                'agent-system-prompt': enrollment['system_prompt']})})) {{const input=document.getElementById(id);
                Object.getOwnPropertyDescriptor(id==='agent-system-prompt'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value').set.call(input,value);
                input.dispatchEvent(new Event('input',{{bubbles:true}}));}} return true;}})()''')
            assert evaluate(f'Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()==={json.dumps(create_label)}).disabled')
            shot('add-agent', 'team-loading-draft')
            fixture['teams_mode'] = 'error'
            fixture['teams_release'].set()
            wait(f'Boolean(document.querySelector("[role=dialog] [role=alert]")) && document.body.innerText.includes({json.dumps(team_error)})')
            assert not evaluate('Boolean(document.querySelector("[role=dialog] #agent-team"))')
            assert evaluate(f'Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()==={json.dumps(create_label)}).disabled')
            shot('add-agent', 'team-error-draft')
            fixture['teams_mode'] = 'populated'
            evaluate(f'Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()==={json.dumps(retry)}).click()')
            wait('Boolean(document.querySelector("[role=dialog] #agent-team option[value=default]"))')
            preserved_enrollment = evaluate('({name:document.getElementById("agent-name").value,description:document.getElementById("agent-description").value,system_prompt:document.getElementById("agent-system-prompt").value})')
            assert preserved_enrollment == enrollment
            evaluate('''(() => {const team=document.getElementById('agent-team');team.value='default';
                team.dispatchEvent(new Event('change',{bubbles:true}));const prompt=document.getElementById('agent-system-prompt');
                prompt.focus();prompt.setSelectionRange(1,7);return true;})()''')
            assert evaluate('document.activeElement.id==="agent-system-prompt" && document.activeElement.selectionStart===1 && document.activeElement.selectionEnd===7')
            wait(f'!Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()==={json.dumps(create_label)}).disabled')
            shot('add-agent', 'team-retry-preserved-draft')
            creates_before = sum(method == 'POST' and path == '/api/v1/orgs/test/agents' for method, path in fixture['requests'])
            evaluate(f'Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()==={json.dumps(create_label)}).click()')
            wait('location.pathname.endsWith("/browser_worker") && !document.querySelector("[role=dialog]")')
            actual_worker = next(row for row in httpx.get(_base(port) + '/agents', headers=_auth_headers())
                .raise_for_status().json()['agents'] if row['name'] == enrollment['name'])
            assert actual_worker['team'] == 'default' and actual_worker['role'] == 'worker'
            assert actual_worker['description'] == enrollment['description']
            assert sum(method == 'POST' and path == '/api/v1/orgs/test/agents' for method, path in fixture['requests']) == creates_before + 1
            shot('add-agent', 'original-create-worker-readback')
            pw('goto', base + '/orgs/test/work-hours')
            wait('Boolean(document.querySelector("table")) && document.body.innerText.includes("consultant_head") && document.body.innerText.includes("consultant_codex")')
            assert not evaluate('Array.from(document.querySelectorAll("tbody tr")).some(e=>e.innerText.includes("founder")||e.innerText.includes("null"))')
            shot('work-hours', 'populated')
            # Existing manager control remains eligible through the same
            # real roster/API gate. No publication/admin action is taken.
            allowed = httpx.get(_base(port) + '/agents/engineering_head/team-escalation-policy', headers=_auth_headers())
            assert allowed.status_code == 200
            pw('goto', base + '/orgs/test/agents/engineering_head')
            policy_label = 'Open team escalation policy' if locale == 'en' else '打开团队上报策略'
            wait(f'Array.from(document.querySelectorAll("a")).some(e=>e.textContent.trim()==={json.dumps(policy_label)})')
            shot('agents', 'eligible-engineering-manager')
            # Direct existing policy view/API stays ineligible for the worker.
            denied = httpx.get(_base(port) + '/agents/consultant_head/team-escalation-policy', headers=_auth_headers())
            assert denied.status_code == 404
            pw('goto', base + '/orgs/test/agents/consultant_head/team-escalation-policy')
            wait('!document.querySelector("[data-testid=team-escalation-policy]") && Boolean(document.querySelector("main a"))')
            shot('policy', 'ineligible')
            (tmp_path / 'C10-browser-receipt.json').write_text(json.dumps({
                'source_sha': binding['revision'], 'cli': cli, 'cli_version': cli_version,
                'browser_user_agent': evaluate('navigator.userAgent'), 'screenshots': evidence,
                'requests': fixture['requests'], 'draft_retention': retained, 'direct_policy_status': denied.status_code,
                'enrollment_draft_retention': preserved_enrollment, 'created_worker': {
                    key: actual_worker[key] for key in ('name', 'team', 'role', 'description')},
                'engineering_policy_status': allowed.status_code,
            }, sort_keys=True))
        finally:
            fixture['release'].set()
            pw('close')
