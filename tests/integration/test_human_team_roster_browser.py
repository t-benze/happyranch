"""C10: existing built SPA and real isolated daemon; four finite parameters.

Run only through TASK10394's finite disposable-hosted browser release. Transport
availability/empty projections are labelled explicitly; they are not migration
receipts. No browser/download/new runner is invoked by authoring this file.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import threading
import time
from urllib.parse import unquote, urlsplit

import httpx
import pytest
import yaml

from tests.integration.conftest import seed_workspace
from tests.integration.test_human_team_roster_e2e import human_daemon, _auth_headers, _base

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _c10_product_control(runtime: Path) -> None:
    """Browser-owned valid pre-attach control; C1–C9 fixtures stay unchanged.

    The shared seed has Engineering/engineering_head and Content/content_manager.
    Product is added here, not renamed from either original control.
    """
    path = runtime / 'org/teams.yaml'
    roster = yaml.safe_load(path.read_text())
    assert roster['teams']['engineering']['manager'] == 'engineering_head'
    assert roster['teams']['content']['manager'] == 'content_manager'
    assert 'product' not in roster['teams']
    roster['teams']['product'] = {'manager': 'product_head', 'workers': []}
    path.write_text(yaml.safe_dump(roster))
    seed_workspace(runtime, 'product_head')


@contextmanager
def _spa(dist: Path, daemon_port: int):
    """Own HTTP availability at this proxy; successful actions go to daemon."""
    state = {'mode': 'populated', 'teams_mode': 'populated', 'settings_mode': 'populated',
             'policy_mode': 'populated', 'requests': [], 'inflight': 0,
             'release': threading.Event(), 'teams_release': threading.Event(),
             'settings_release': threading.Event(), 'policy_release': threading.Event()}
    lock = threading.Lock()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Never log bearer credentials.

        def do_GET(self):
            self.respond()

        def do_POST(self):
            self.respond()

        def do_PUT(self):
            self.respond()

        def send_json(self, code: int, raw: bytes):
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def respond(self):
            path = urlsplit(self.path).path
            if path.startswith('/api/'):
                body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
                with lock:
                    record = {'method': self.command, 'path': path, 'target': self.path,
                              'started': time.monotonic(), 'body': json.loads(body) if body else None}
                    state['requests'].append(record)
                    state['inflight'] += 1
                try:
                    query = None
                    if path == '/api/v1/orgs/test/agents':
                        query = ('mode', 'release')
                    elif path == '/api/v1/orgs/test/teams':
                        query = ('teams_mode', 'teams_release')
                    elif path == '/api/v1/orgs/test/settings':
                        query = ('settings_mode', 'settings_release')
                    elif path == '/api/v1/orgs/test/agents/engineering_head/team-escalation-policy':
                        query = ('policy_mode', 'policy_release')
                    if self.command == 'GET' and query:
                        mode, release = query
                        if state[mode] == 'loading' and not state[release].wait(30):
                            record['status'] = 504
                            self.send_json(504, b'{"detail":"owned proxy deadline"}')
                            return
                        if state[mode] == 'error':
                            record['status'] = 503
                            self.send_json(503, b'{"detail":"owned C10 HTTP outage"}')
                            return
                        if mode == 'mode' and state[mode] == 'empty-agents':
                            record['status'] = 200
                            self.send_json(200, b'{"agents":[]}')
                            return
                    if self.command == 'GET' and query and query[0] == 'teams_mode' and state['teams_mode'] == 'empty-teams':
                        record['status'] = 200
                        self.send_json(200, b'{"teams":[]}')
                        return
                    connection = http.client.HTTPConnection('127.0.0.1', daemon_port, timeout=30)
                    try:
                        headers = {key: value for key, value in self.headers.items()
                                   if key.lower() not in ('host', 'connection', 'content-length')}
                        connection.request(self.command, self.path, body, headers)
                        response = connection.getresponse()
                        raw = response.read()
                        # Coherent UI empty-team projection, not a hidden live
                        # roster mutation. Other real teams/agents stay populated.
                        if self.command == 'GET' and response.status == 200 and state['mode'] == 'empty-default':
                            data = json.loads(raw)
                            if path == '/api/v1/orgs/test/agents':
                                data['agents'] = [row for row in data['agents'] if row['team'] != 'default']
                                raw = json.dumps(data).encode()
                            elif path == '/api/v1/orgs/test/teams':
                                for row in data['teams']:
                                    if row['name'] == 'default':
                                        assert row['manager'] is None and row['manager_kind'] == 'human'
                                        row['workers'] = []
                                raw = json.dumps(data).encode()
                        record['status'] = response.status
                        self.send_json(response.status, raw)
                    finally:
                        connection.close()
                finally:
                    with lock:
                        record['finished'] = time.monotonic()
                        state['inflight'] -= 1
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
        for key in ('release', 'teams_release', 'settings_release', 'policy_release'):
            state[key].set()
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
    dist = Path(binding['source']) / 'web/dist'
    assert (dist / 'index.html').is_file(), 'existing supported Web build required'
    cli = shutil.which('playwright-cli')
    assert cli is not None, 'existing authorized disposable browser/CLI required; no download/fallback'
    selected = binding['roster']['browser']
    assert cli == str(Path(binding['root']) / 'bin/playwright-cli')
    assert hashlib.sha256(Path(cli).read_bytes()).hexdigest() == selected['playwright-cli_shim_sha256']
    node = shutil.which('node')
    assert node == str(Path(binding['root']) / 'bin/node')
    assert hashlib.sha256(Path(node).read_bytes()).hexdigest() == selected['node_shim_sha256']
    assert hashlib.sha256(Path(selected['cli']).read_bytes()).hexdigest() == selected['cli_sha256']
    assert hashlib.sha256(Path(selected['node']).read_bytes()).hexdigest() == selected['node_sha256']
    assert hashlib.sha256(Path(selected['browser']).read_bytes()).hexdigest() == selected['browser_sha256']
    assert hashlib.sha256(Path(selected['config']).read_bytes()).hexdigest() == selected['config_sha256']
    launch_options = json.loads(Path(selected['config']).read_text())['browser']['launchOptions']
    assert launch_options['chromiumSandbox'] is True
    assert launch_options['executablePath'] == selected['browser']
    assert subprocess.run([node, '--version'], check=True, text=True, capture_output=True, timeout=15).stdout.strip() == selected['node_version']
    cli_version = subprocess.run([cli, '--version'], check=True, text=True, capture_output=True, timeout=15).stdout.strip()
    assert cli_version == '0.1.18'

    port, root = human_daemon
    assert root.is_relative_to(Path(binding['root']))
    session = 'c10-' + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:16]
    evidence, retention, locale_windows = [], [], []
    canonical_before = (root / 'org/teams.yaml').read_bytes()

    def pw(*arguments: str) -> str:
        actual = subprocess.run([cli, '-s=' + session, *arguments], text=True, capture_output=True, timeout=40)
        # Retain the real CLI status before assertions/teardown, including a
        # sandbox launch refusal. These are fixture calls, never ambient logs.
        with (tmp_path / 'C10-cli.calls.jsonl').open('a') as output:
            output.write(json.dumps({'command': actual.args, 'exit': actual.returncode,
                'stdout': actual.stdout, 'stderr': actual.stderr,
                'source_sha': binding['revision'], 'launch_options': launch_options}) + '\n')
        assert actual.returncode == 0, (arguments[0], actual.returncode, actual.stdout, actual.stderr)
        return actual.stdout

    def evaluate(expression: str):
        return _result(pw('eval', 'async () => JSON.stringify(await (' + expression + '))'))

    def action(code: str):
        # Playwright's normal pointer/keyboard/input behavior, including focus.
        pw('run-code', 'async page => {' + code + '}')

    def click(role: str, label: str, scope: str = ''):
        action('await page' + scope + '.getByRole(' + json.dumps(role) + ',{name:' + json.dumps(label) + ',exact:true}).click();')

    def fill(selector: str, value: str):
        action('await page.locator(' + json.dumps(selector) + ').fill(' + json.dumps(value) + ');')

    def wait(expression: str):
        deadline = time.monotonic() + 20
        actual = None
        while time.monotonic() < deadline:
            actual = evaluate(expression)
            if actual:
                return actual
            time.sleep(0.1)
        raise AssertionError((expression, actual))

    def settled():
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if fixture['inflight'] == 0:
                time.sleep(0.2)
                if fixture['inflight'] == 0:
                    return
            time.sleep(0.05)
        raise AssertionError('baseline HTTP did not settle')

    def shot(view: str, state: str, controls: tuple[str, ...] = ()):
        geometry = evaluate('({width:innerWidth,height:innerHeight,documentWidth:document.documentElement.scrollWidth})')
        assert geometry['width'] == viewport[0] and geometry['height'] == viewport[1]
        assert geometry['documentWidth'] <= viewport[0]
        visibility = []
        for selector in controls:
            # Scroll normal overflow containers to the real action, then prove
            # bounds AND hit testing; document width alone is insufficient.
            action('await page.locator(' + json.dumps(selector) + ').scrollIntoViewIfNeeded();')
            bounds = evaluate('''(() => {const e=document.querySelector(''' + json.dumps(selector) + ''');
                const r=e.getBoundingClientRect(), x=r.left+r.width/2,y=r.top+r.height/2;
                const hit=document.elementFromPoint(x,y);return {width:r.width,height:r.height,
                left:r.left,right:r.right,top:r.top,bottom:r.bottom,hit:hit===e||e.contains(hit),
                name:e.getAttribute('aria-label')||e.textContent.trim()};})()''')
            assert bounds['width'] > 0 and bounds['height'] > 0 and bounds['hit'], (selector, bounds)
            assert 0 <= bounds['left'] and bounds['right'] <= viewport[0] and 0 <= bounds['top'] and bounds['bottom'] <= viewport[1]
            visibility.append({'selector': selector, **bounds})
        display_locale = evaluate('document.documentElement.lang')
        assert display_locale in ('en', 'zh-CN')
        target = tmp_path / f'C10-{display_locale}-{viewport[0]}x{viewport[1]}-{view}-{state}.png'
        pw('screenshot', '--filename=' + str(target))
        assert target.is_file() and target.stat().st_size > 0
        evidence.append({'view': view, 'state': state, 'locale': display_locale, 'parameter_locale': locale, 'screenshot': str(target),
                         'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                         'geometry': geometry, 'action_visibility': visibility})

    labels = {
        'en': {'founder': 'Managed by Founder', 'language': 'Language', 'model': 'Model', 'save': 'Save agent', 'startThread': 'Start Thread',
            'subject': 'Subject', 'recipients': 'Recipients (comma-separated agent names)', 'body': 'Body (Markdown)', 'send': 'Send',
            'retry': 'Retry', 'error': 'Could not load agents.', 'empty': 'No agents enrolled', 'new': 'New agent',
            'add': 'Add agent', 'create': 'Create', 'close': 'Close', 'loadingTeam': 'Loading teams…',
            'errorTeam': 'Could not load teams.', 'noTeams': 'No teams yet. Add a manager to create the first team.', 'policyOpen': 'Open team escalation policy',
            'to': 'What to escalate', 'not': 'What not to escalate', 'policySave': 'Save & activate',
            'confirm': 'Confirm save & activate', 'stay': 'Stay on page', 'discard': 'Discard and continue',
            'cancel': 'Cancel', 'policyLoading': 'Loading team policy…', 'policyError': 'Could not load the team policy.',
            'whLoading': 'Loading work hours…', 'whRosterLoading': 'Loading the Work Hours roster…',
            'whRosterError': 'Could not load the Work Hours roster.', 'whEmpty': 'No agents',
            'whRecovery': 'Live config failed to load. Scheduling is degraded.', 'whRegion': 'Work Hours roster table', 'editTeam': 'Edit team…', 'reviewImpact': 'Review impact…', 'confirmSchedule': 'Confirm & save'},
        'zh-CN': {'founder': '由创始人管理', 'language': '语言', 'model': '模型', 'save': '保存智能体', 'startThread': '发起会话',
            'subject': '主题', 'recipients': '收件人（以逗号分隔的智能体名称）', 'body': '正文（Markdown）', 'send': '发送',
            'retry': '重试', 'error': '无法加载智能体。', 'empty': '尚未登记智能体', 'new': '新建智能体',
            'add': '添加智能体', 'create': '创建', 'close': '关闭', 'loadingTeam': '正在加载团队…',
            'errorTeam': '无法加载团队。', 'noTeams': '还没有团队。请添加一名经理来创建第一个团队。', 'policyOpen': '打开团队上报策略',
            'to': '需要上报的情况', 'not': '无需上报的情况', 'policySave': '保存并激活',
            'confirm': '确认保存并激活', 'stay': '留在此页', 'discard': '放弃并继续', 'cancel': '取消',
            'policyLoading': '正在加载团队策略…', 'policyError': '无法加载团队策略。',
            'whLoading': '正在加载工时…', 'whRosterLoading': '正在加载工时名册…',
            'whRosterError': '无法加载工时名册。', 'whEmpty': '暂无智能体',
            'whRecovery': '实时配置加载失败，调度已降级。', 'whRegion': '工时智能体列表', 'editTeam': '编辑团队…', 'reviewImpact': '查看影响…', 'confirmSchedule': '确认并保存'},
    }
    text = labels[locale]

    def contains(value: str):
        return 'document.body.innerText.includes(' + json.dumps(value) + ')'

    def locale_window(index: int, old: str, new: str, focus_contract: str):
        window = fixture['requests'][index:]
        # Only the existing jobs/tasks ten-second timers can be unrelated to
        # this user action. Their due-time witness is retained, never ignored.
        polling = []
        for request in window:
            prior = [row for row in fixture['requests'][:index] if row['method'] == request['method'] and row['target'] == request['target']]
            assert request['method'] == 'GET' and request['path'] in ('/api/v1/orgs/test/jobs/', '/api/v1/orgs/test/tasks')
            assert prior and 9 <= request['started'] - prior[-1]['started'] <= 12, request
            polling.append(request)
        locale_windows.append({'from': old, 'to': new, 'focus_contract': focus_contract,
                               'locale_triggered_requests': [], 'unrelated_due_polling': polling})

    def switch(old: str, new: str):
        settled()
        index = len(fixture['requests'])
        # CSS consumes the inner quoted value; JSON Unicode escapes are JS,
        # not CSS escapes. Preserve literal labels before the outer JS quoting.
        trigger = '[aria-label=' + json.dumps(labels[old]['language'], ensure_ascii=False) + ']'
        action('await page.locator(' + json.dumps(trigger) + ').click();')
        click('option', '简体中文' if new == 'zh-CN' else 'English')
        wait('document.documentElement.lang===' + json.dumps(new))
        focused = wait('document.activeElement===document.querySelector(' + json.dumps('[aria-label=' + json.dumps(labels[new]['language'], ensure_ascii=False) + ']') + ')')
        time.sleep(0.2)
        locale_window(index, old, new, 'shipped header trigger focused: ' + str(focused))

    def policy_rows():
        with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as conn:
            return {table: conn.execute(f'SELECT * FROM {table} ORDER BY rowid').fetchall() for table in (
                'authority_policy_v2_releases', 'authority_policy_v2_activations', 'authority_policy_active_selector',
                'authority_policy_active_selector_history', 'authority_policy_v2_control_audit')}

    with _spa(dist, port) as (base, fixture):
        opened = False
        try:
            pw('open', '--config=' + selected['config'])
            opened = True
            pw('resize', str(viewport[0]), str(viewport[1]))
            pw('goto', base + '/orgs/test/agents/consultant_head')
            pw('localstorage-set', 'happyranch.ui.locale', locale)
            pw('reload')
            # Both workers: the real model editor survives locale changes; the
            # original Save causes a genuine roster-query refresh after write.
            for agent in ('consultant_head', 'consultant_codex'):
                pw('goto', base + '/orgs/test/agents/' + agent)
                selector = 'input[aria-label=' + json.dumps(text['model'], ensure_ascii=False) + ']'
                wait('document.documentElement.lang===' + json.dumps(locale) + ' && Boolean(document.querySelector(' + json.dumps(selector) + '))')
                assert evaluate(contains(text['founder']))
                assert not evaluate('Boolean(document.querySelector("[data-testid=team-escalation-policy]"))')
                settled()
                assert not any(row['path'].endswith('/' + agent + '/team-escalation-policy') for row in fixture['requests'])
                draft = 'c10-' + agent + '-draft'
                fill(selector, draft)
                evaluate('(() => {window.__c10Model=document.querySelector(' + json.dumps(selector) + ');window.__c10Model.setSelectionRange(2,6);return true;})()')
                assert evaluate('document.activeElement===window.__c10Model')
                next_locale = 'zh-CN' if locale == 'en' else 'en'
                switch(locale, next_locale)
                next_selector = 'input[aria-label=' + json.dumps(labels[next_locale]['model'], ensure_ascii=False) + ']'
                retained = evaluate('({same:document.querySelector(' + json.dumps(next_selector) + ')===window.__c10Model,value:window.__c10Model.value,start:window.__c10Model.selectionStart,end:window.__c10Model.selectionEnd,editorFocused:document.activeElement===window.__c10Model,selected:location.pathname})')
                assert retained == {'same': True, 'value': draft, 'start': 2, 'end': 6,
                                    'editorFocused': False, 'selected': '/orgs/test/agents/' + agent}
                action('await page.locator(' + json.dumps(next_selector) + ').focus();')
                assert evaluate('document.activeElement===window.__c10Model && window.__c10Model.selectionStart===2 && window.__c10Model.selectionEnd===6')
                retention.append({'agent': agent, 'filter': 'not applicable: AgentsPage has no roster filter', **retained})
                switch(next_locale, locale)
                shot('agents-detail', 'draft-' + agent, (selector, 'footer button:last-child'))
                reads_before = sum(row['path'] == '/api/v1/orgs/test/agents' and row['method'] == 'GET' for row in fixture['requests'])
                writes_before = len([row for row in fixture['requests'] if row['method'] != 'GET'])
                click('button', text['save'])
                wait('!Array.from(document.querySelectorAll("button")).some(e=>e.textContent.trim()===' + json.dumps(text['save']) + ')')
                settled()
                agents = httpx.get(_base(port) + '/agents', headers=_auth_headers()).raise_for_status().json()['agents']
                assert next(row for row in agents if row['name'] == agent)['model'] == draft
                new_writes = [row for row in fixture['requests'] if row['method'] != 'GET'][writes_before:]
                assert [(row['method'], row['path'], row['body']) for row in new_writes] == [
                    ('PUT', '/api/v1/orgs/test/agents/' + agent + '/model', {'model': draft})]
                assert sum(row['path'] == '/api/v1/orgs/test/agents' and row['method'] == 'GET' for row in fixture['requests']) > reads_before
                assert evaluate('document.querySelector(' + json.dumps(selector) + ')===window.__c10Model && location.pathname.endsWith(' + json.dumps(agent) + ')')
                shot('agents-detail', 'populated-' + agent, (selector,))

                # Existing worker recipient picker; roster refresh is observed
                # above. Open-modal query invalidation is owned by the Web
                # QueryClient case, since this SPA has no modal refresh action.
                click('button', text['startThread'])
                wait('Boolean(document.querySelector(' + json.dumps('[role=dialog] input[placeholder="agent_a, agent_b"]') + '))')
                recipient_selector = '[role=dialog] input[placeholder="agent_a, agent_b"]'
                assert evaluate('document.querySelector(' + json.dumps(recipient_selector) + ').value===' + json.dumps(agent))
                subject = 'C10 exact subject ' + agent
                body_text = 'C10 exact body / 原文 ' + agent
                action('await page.getByRole("dialog").getByLabel(' + json.dumps(text['subject']) + ',{exact:true}).fill(' + json.dumps(subject) + ');'
                       'await page.getByRole("dialog").getByLabel(' + json.dumps(text['body']) + ',{exact:true}).fill(' + json.dumps(body_text) + ');')
                fill(recipient_selector, 'consultant_')
                wait('document.querySelectorAll("[role=option]").length===2')
                assert evaluate('Array.from(document.querySelectorAll("[role=option]")).every(e=>e.innerText.includes("default")&&!e.innerText.includes("founder"))')
                evaluate('(() => {window.__c10Recipient=document.querySelector(' + json.dumps(recipient_selector) + ');return true;})()')
                assert evaluate('document.activeElement===window.__c10Recipient && window.__c10Recipient.selectionStart===11 && window.__c10Recipient.selectionEnd===11')
                settled(); index = len(fixture['requests'])
                # Documented external-tab locale mirror preserves modal focus.
                evaluate('(() => {const v=' + json.dumps(next_locale) + ';localStorage.setItem("happyranch.ui.locale",v);window.dispatchEvent(new StorageEvent("storage",{key:"happyranch.ui.locale",newValue:v,storageArea:localStorage}));return true;})()')
                wait('document.documentElement.lang===' + json.dumps(next_locale))
                time.sleep(0.2)
                locale_window(index, locale, next_locale, 'external-tab modal recipient remains focused')
                assert evaluate('document.querySelector(' + json.dumps(recipient_selector) + ')===window.__c10Recipient&&document.activeElement===window.__c10Recipient&&window.__c10Recipient.value==="consultant_"&&window.__c10Recipient.selectionStart===11&&window.__c10Recipient.selectionEnd===11')
                action('if(await page.getByRole("dialog").getByLabel(' + json.dumps(labels[next_locale]['subject']) + ',{exact:true}).inputValue()!==' + json.dumps(subject) + ')throw new Error("subject lost");'
                       'if(await page.getByRole("dialog").getByLabel(' + json.dumps(labels[next_locale]['body']) + ',{exact:true}).inputValue()!==' + json.dumps(body_text) + ')throw new Error("body lost");')
                action('await page.getByRole("option",{name:' + json.dumps(agent + ' default') + ',exact:true}).click();')
                wait('window.__c10Recipient.value===' + json.dumps(agent + ', '))
                shot('worker-selector', 'filtered-selected-' + agent, (recipient_selector, '[role=dialog] > div.flex-row > button:last-child'))
                writes_before = len([row for row in fixture['requests'] if row['method'] != 'GET'])
                click('button', labels[next_locale]['send'], '.getByRole("dialog")')
                wait('location.pathname.includes("/threads/THR-")&&!document.querySelector("[role=dialog]")')
                settled()
                new_writes = [row for row in fixture['requests'] if row['method'] != 'GET'][writes_before:]
                assert [(row['method'], row['path'], row['body']) for row in new_writes] == [
                    ('POST', '/api/v1/orgs/test/threads', {'subject': subject, 'recipients': [agent], 'body_markdown': body_text})]
                thread_id = evaluate('location.pathname.split("/").at(-1)')
                thread = httpx.get(_base(port) + '/threads/' + thread_id, headers=_auth_headers()).raise_for_status().json()
                assert thread['subject'] == subject and thread['participants'] == [agent]
                assert any(message['body_markdown'] == body_text for message in thread['messages'])
                retention.append({'agent': agent, 'control': 'worker-recipient', 'filter': 'consultant_',
                                  'locale_mode': 'documented external-tab mirror', 'selected': agent,
                                  'source_node_same': True, 'focused': True, 'selection': [11, 11],
                                  'original_write': new_writes[0], 'canonical_thread_id': thread_id})
                pw('localstorage-set', 'happyranch.ui.locale', locale)

            # Direct server denials and unchanged canonical policy rows, rather
            # than treating hidden editor controls as authorization evidence.
            policy_before = policy_rows()
            denials = []
            for agent in ('consultant_head', 'consultant_codex'):
                denied = httpx.get(_base(port) + '/agents/' + agent + '/team-escalation-policy', headers=_auth_headers())
                assert denied.status_code == 404
                mutation = httpx.post(_base(port) + '/agents/' + agent + '/team-escalation-policy/v2/releases',
                    headers=_auth_headers(), json={'team': 'default', 'policy_id': 'c10-denied', 'title': 'Denied',
                        'what_to_escalate': 'Denied', 'what_not_to_escalate': 'Denied',
                        'create_request_id': 'c10-denied-create-' + agent, 'activation_request_id': 'c10-denied-select-' + agent,
                        'based_on_selector_id': None, 'expected_selector_id': None, 'action': 'bootstrap',
                        'acknowledge_shared_credential_attribution': True})
                assert mutation.status_code == 404
                assert policy_rows() == policy_before
                pw('goto', base + '/orgs/test/agents/' + agent + '/team-escalation-policy')
                wait('!document.querySelector("[data-testid=team-escalation-policy]") && Boolean(document.querySelector("main a"))')
                assert not any(row['path'].endswith('/' + agent + '/team-escalation-policy') for row in fixture['requests'])
                denials.append({'agent': agent, 'get': denied.status_code, 'post': mutation.status_code})
                shot('policy', 'ineligible-' + agent, ('main a',))

            # Agent-list empty is deliberately independent of empty human team.
            for mode in ('loading', 'error', 'empty-agents', 'empty-default'):
                fixture['mode'] = mode
                fixture['release'].clear()
                pw('goto', base + '/orgs/test/agents')
                if mode == 'loading':
                    wait('Boolean(document.querySelector("[role=status].animate-pulse"))')
                elif mode == 'error':
                    wait(contains(text['error']))
                elif mode == 'empty-agents':
                    wait(contains(text['empty']))
                else:
                    wait(contains('engineering_head') + ' && ' + contains('product_head'))
                    assert not evaluate(contains(text['empty']))
                    assert not evaluate(contains('consultant_codex'))
                shot('agents', mode)
                if mode in ('empty-agents', 'empty-default'):
                    click('button', text['add'] if mode == 'empty-agents' else text['new'])
                    wait('Boolean(document.querySelector("[role=dialog] #agent-team"))')
                    option = evaluate('Array.from(document.querySelectorAll("#agent-team option")).find(e=>e.value==="default").textContent')
                    assert 'default' in option and text['founder'] in option
                    assert not evaluate('Array.from(document.querySelectorAll("#agent-team option")).some(e=>e.value==="founder"||e.value==="consultant")')
                    if mode == 'empty-default':
                        assert evaluate('Array.from(document.querySelectorAll("#agent-team option")).some(e=>e.value==="product")')
                    shot('add-agent', mode, ('#agent-team',))
                    click('button', text['close'], '.getByRole("dialog")')
                if mode in ('loading', 'error'):
                    fixture['mode'] = 'populated'
                    fixture['release'].set()
                    if mode == 'error':
                        click('button', text['retry'])
                    wait(contains('consultant_codex'))
                    shot('agents', mode + '-recovered')
                fixture['release'].set()
            fixture['mode'] = 'populated'
            fixture['teams_mode'] = 'empty-teams'
            pw('goto', base + '/orgs/test/agents')
            wait(contains('consultant_head'))
            click('button', text['new'])
            wait(contains(text['noTeams']))
            assert evaluate('Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()===' + json.dumps(text['create']) + ').disabled')
            shot('add-agent', 'successful-empty-teams')
            click('button', text['close'], '.getByRole("dialog")')
            fixture['teams_mode'] = 'loading'
            fixture['teams_release'].clear()
            pw('goto', base + '/orgs/test/agents')
            wait(contains('consultant_head'))
            click('button', text['new'])
            wait(contains(text['loadingTeam']))
            enrollment = {'name': 'browser_worker', 'description': 'Browser worker draft', 'system_prompt': 'Preserved browser prompt'}
            for key, value in enrollment.items():
                fill({'name': '#agent-name', 'description': '#agent-description', 'system_prompt': '#agent-system-prompt'}[key], value)
            assert evaluate('Array.from(document.querySelectorAll("[role=dialog] button")).find(e=>e.textContent.trim()===' + json.dumps(text['create']) + ').disabled')
            shot('add-agent', 'team-loading-draft', ('#agent-system-prompt',))
            fixture['teams_mode'] = 'error'
            fixture['teams_release'].set()
            wait(contains(text['errorTeam']))
            shot('add-agent', 'team-error-draft', ('#agent-system-prompt',))
            fixture['teams_mode'] = 'populated'
            click('button', text['retry'], '.getByRole("dialog")')
            wait('Boolean(document.querySelector("#agent-team option[value=default]"))')
            action('await page.locator("#agent-team").selectOption("default");await page.locator("#agent-executor").selectOption("claude");')
            preserved_enrollment = evaluate('({name:document.getElementById("agent-name").value,description:document.getElementById("agent-description").value,system_prompt:document.getElementById("agent-system-prompt").value})')
            assert preserved_enrollment == enrollment
            action('await page.locator("#agent-system-prompt").focus();')
            evaluate('(() => {window.__c10Prompt=document.getElementById("agent-system-prompt");window.__c10Prompt.setSelectionRange(1,7);return true;})()')
            # The header is outside the modal focus trap. Close would discard
            # this draft; use the documented storage-event mirror instead of
            # pretending the header can be reached through an open modal.
            next_locale = 'zh-CN' if locale == 'en' else 'en'
            settled(); request_index = len(fixture['requests'])
            evaluate('(() => {const v=' + json.dumps(next_locale) + ';localStorage.setItem("happyranch.ui.locale",v);window.dispatchEvent(new StorageEvent("storage",{key:"happyranch.ui.locale",newValue:v,storageArea:localStorage}));return true;})()')
            wait('document.documentElement.lang===' + json.dumps(next_locale))
            assert evaluate('document.activeElement===window.__c10Prompt && window.__c10Prompt.value==="Preserved browser prompt" && window.__c10Prompt.selectionStart===1 && window.__c10Prompt.selectionEnd===7 && document.getElementById("agent-team").value==="default"&&document.getElementById("agent-executor").value==="claude"')
            time.sleep(0.2)
            locale_window(request_index, locale, next_locale, 'external-tab enrollment prompt remains focused')
            # Return through the same documented external-tab contract.
            evaluate('(() => {const v=' + json.dumps(locale) + ';localStorage.setItem("happyranch.ui.locale",v);window.dispatchEvent(new StorageEvent("storage",{key:"happyranch.ui.locale",newValue:v,storageArea:localStorage}));return true;})()')
            wait('document.documentElement.lang===' + json.dumps(locale))
            shot('add-agent', 'retry-locale-retained-draft', ('#agent-system-prompt',))
            creates_before = len([row for row in fixture['requests'] if row['method'] != 'GET'])
            click('button', text['create'], '.getByRole("dialog")')
            wait('location.pathname.endsWith("/browser_worker") && !document.querySelector("[role=dialog]")')
            settled()
            actual_worker = next(row for row in httpx.get(_base(port) + '/agents', headers=_auth_headers()).raise_for_status().json()['agents'] if row['name'] == 'browser_worker')
            assert actual_worker['team'] == 'default' and actual_worker['role'] == 'worker'
            assert actual_worker['description'] == enrollment['description'] and actual_worker['system_prompt'] == enrollment['system_prompt']
            creation_writes = [row for row in fixture['requests'] if row['method'] != 'GET'][creates_before:]
            assert [(row['method'], row['path'], row['body']) for row in creation_writes] == [
                ('POST', '/api/v1/orgs/test/agents', {**enrollment, 'role': 'worker', 'team': 'default', 'executor': 'claude'})]
            shot('add-agent', 'original-create-readback')

            # Work Hours normal settings and membership data come from daemon.
            for mode in ('loading', 'error', 'populated'):
                fixture['settings_mode'] = mode; fixture['settings_release'].clear()
                pw('goto', base + '/orgs/test/work-hours')
                if mode == 'loading':
                    wait(contains(text['whLoading'])); shot('work-hours', 'settings-loading')
                    fixture['settings_mode'] = 'populated'; fixture['settings_release'].set()
                elif mode == 'error':
                    wait(contains(text['whRecovery'])); shot('work-hours', 'settings-error')
                    # Overview's config-recovery banner has no Retry/editor.
                    # Real navigation/reload is the supported recovery control.
                    fixture['settings_mode'] = 'populated'; pw('reload')
                wait('Boolean(document.querySelector("table")) && ' + contains('consultant_codex'))
                assert not evaluate('Array.from(document.querySelectorAll("tbody tr")).some(e=>e.innerText.includes("founder")||e.innerText.includes("null"))')
                for agent in ('consultant_head', 'consultant_codex'):
                    assert evaluate('Array.from(document.querySelectorAll("tbody tr")).find(e=>e.innerText.includes(' + json.dumps(agent) + ')).children[1].textContent.trim()==="default"')
                shot('work-hours', 'settings-' + mode + '-recovered', ('[role=region]',))
            for mode in ('loading', 'error', 'empty-agents', 'empty-default'):
                fixture['mode'] = mode; fixture['release'].clear()
                pw('goto', base + '/orgs/test/work-hours')
                expected = text['whRosterLoading'] if mode == 'loading' else text['whRosterError'] if mode == 'error' else text['whEmpty'] if mode == 'empty-agents' else 'engineering_head'
                wait(contains(expected)); shot('work-hours', mode)
                fixture['mode'] = 'populated'; fixture['release'].set()
                if mode == 'error': click('button', text['retry'])
                elif mode in ('empty-agents', 'empty-default'): pw('reload')
                wait('Boolean(document.querySelector("table")) && ' + contains('consultant_codex'))
                shot('work-hours', mode + '-recovered')
            # Keyboard scrolling proves that the final column can be reached.
            region = '[role=region][aria-label=' + json.dumps(text['whRegion'], ensure_ascii=False) + ']'
            action('await page.locator(' + json.dumps(region) + ').focus();for(let i=0;i<30;i++) await page.locator(' + json.dumps(region) + ').press("ArrowRight");')
            wait('(() => {const e=document.querySelector(' + json.dumps(region) + '),r=e.getBoundingClientRect(),h=e.querySelector("thead th:last-child").getBoundingClientRect();return document.activeElement===e&&h.right<=r.right&&h.left>=r.left;})()')
            shot('work-hours', 'last-column-reachable', (region,))

            # Existing team-tier editor: no enabled/eligibility change. The
            # original supported action writes only this isolated team draft.
            settings_before = httpx.get(_base(port) + '/settings', headers=_auth_headers()).raise_for_status().json()['org']['working_hours']
            raw_layer = settings_before['teams'].get('default', {
                'mode': None, 'interval': None, 'window': {'start': None, 'end': None, 'timezone': None},
                'days': None, 'catch_up_on_startup': None})
            exact_layer = {**raw_layer, 'interval': '4h'}
            action('await page.getByRole("combobox").filter({hasText:' + json.dumps(text['editTeam']) + '}).click();')
            click('option', 'default · ' + text['founder'])
            schedule_selector = '[role=dialog] input[placeholder="2h"]'
            wait('Boolean(document.querySelector(' + json.dumps(schedule_selector) + '))')
            fill(schedule_selector, '4h')
            evaluate('(() => {window.__c10Interval=document.querySelector(' + json.dumps(schedule_selector) + ');window.__c10Interval.setSelectionRange(0,2);return true;})()')
            settled(); index = len(fixture['requests'])
            next_locale = 'zh-CN' if locale == 'en' else 'en'
            evaluate('(() => {const v=' + json.dumps(next_locale) + ';localStorage.setItem("happyranch.ui.locale",v);window.dispatchEvent(new StorageEvent("storage",{key:"happyranch.ui.locale",newValue:v,storageArea:localStorage}));return true;})()')
            wait('document.documentElement.lang===' + json.dumps(next_locale))
            time.sleep(0.2)
            locale_window(index, locale, next_locale, 'external-tab team interval remains focused')
            assert evaluate('document.querySelector(' + json.dumps(schedule_selector) + ')===window.__c10Interval&&document.activeElement===window.__c10Interval&&window.__c10Interval.value==="4h"&&window.__c10Interval.selectionStart===0&&window.__c10Interval.selectionEnd===2')
            shot('work-hours-editor', 'default-locale-retained-draft', (schedule_selector, '[role=dialog] > div.flex-row > button:last-child'))
            writes_before = len([row for row in fixture['requests'] if row['method'] != 'GET'])
            click('button', labels[next_locale]['reviewImpact'], '.getByRole("dialog")')
            wait(contains('consultant_head') + ' && ' + contains('consultant_codex'))
            shot('work-hours-editor', 'default-confirmation', ('[role=dialog] > div.flex-row > button:last-child',))
            click('button', labels[next_locale]['confirmSchedule'], '.getByRole("dialog")')
            wait('!document.querySelector("[role=dialog]")')
            settled()
            new_writes = [row for row in fixture['requests'] if row['method'] != 'GET'][writes_before:]
            assert [(row['method'], row['path'], row['body']) for row in new_writes] == [
                ('PUT', '/api/v1/orgs/test/settings/org', {'working_hours': {'teams': {'default': exact_layer}}})]
            settings_after = httpx.get(_base(port) + '/settings', headers=_auth_headers()).raise_for_status().json()['org']['working_hours']
            assert settings_after == {**settings_before, 'teams': {**settings_before['teams'], 'default': exact_layer}}
            retention.append({'control': 'Work Hours team tier', 'team': 'default', 'draft': exact_layer,
                              'locale_mode': 'documented external-tab mirror', 'node_same': True,
                              'focused': True, 'selection': [0, 2], 'original_write': new_writes[0]})
            pw('localstorage-set', 'happyranch.ui.locale', locale)


            # Legacy Engineering and Product remain eligible controls.
            allowed = {}
            for manager in ('engineering_head', 'product_head'):
                projection = httpx.get(_base(port) + '/agents/' + manager + '/team-escalation-policy', headers=_auth_headers()).raise_for_status().json()
                assert projection['target_manager'] == manager
                allowed[manager] = projection
                pw('goto', base + '/orgs/test/agents/' + manager)
                wait('Array.from(document.querySelectorAll("a")).some(e=>e.textContent.trim()===' + json.dumps(text['policyOpen']) + ')')
                shot('agents-detail', 'eligible-' + manager)
            policy_path = base + '/orgs/test/agents/engineering_head/team-escalation-policy'
            for mode in ('loading', 'error'):
                fixture['policy_mode'] = mode; fixture['policy_release'].clear()
                pw('goto', policy_path)
                wait(contains(text['policyLoading'] if mode == 'loading' else text['policyError']))
                shot('policy', mode)
                fixture['policy_mode'] = 'populated'; fixture['policy_release'].set()
                if mode == 'error': click('button', text['retry'])
                wait('document.querySelectorAll("[data-testid=team-escalation-policy] textarea").length===2')
                shot('policy', mode + '-recovered')
            before_policy = httpx.get(_base(port) + '/agents/engineering_head/team-escalation-policy', headers=_auth_headers()).raise_for_status().json()
            assert before_policy['family'] == 'empty' and before_policy['selector_epoch'] == 0
            shot('policy', 'empty-selector-real-starter')
            exact_to = 'C10 escalation draft / 上报\nExact original text.'
            exact_not = 'C10 continuation draft / 继续\nExact original text.'
            to_selector = '[data-testid=team-escalation-policy] > label:first-of-type textarea'
            # Each textarea is inside its own label; use accessible labels.
            action('await page.getByRole("textbox",{name:' + json.dumps(text['to']) + ',exact:true}).fill(' + json.dumps(exact_to) + ');await page.getByRole("textbox",{name:' + json.dumps(text['not']) + ',exact:true}).fill(' + json.dumps(exact_not) + ');await page.getByRole("textbox",{name:' + json.dumps(text['to']) + ',exact:true}).focus();')
            evaluate('(() => {window.__c10Policy=document.querySelector("[data-testid=team-escalation-policy] textarea");window.__c10Policy.setSelectionRange(2,8);return true;})()')
            next_locale = 'zh-CN' if locale == 'en' else 'en'
            switch(locale, next_locale)
            assert evaluate('document.querySelector("[data-testid=team-escalation-policy] textarea")===window.__c10Policy && window.__c10Policy.value===' + json.dumps(exact_to) + '&& window.__c10Policy.selectionStart===2 && window.__c10Policy.selectionEnd===8 && document.activeElement!==window.__c10Policy')
            action('await page.getByRole("textbox",{name:' + json.dumps(labels[next_locale]['to']) + ',exact:true}).focus();')
            assert evaluate('document.activeElement===window.__c10Policy')
            switch(next_locale, locale)
            shot('policy', 'engineering-locale-draft', (to_selector,))
            # Shipping dirty-navigation blocker; no write on Stay or Cancel.
            action('await page.locator("section[aria-labelledby=team-policy-page-heading] > a").click();')
            wait('Boolean(document.querySelector("[role=dialog]"))')
            shot('policy', 'dirty-navigation-stay')
            click('button', text['stay'], '.getByRole("dialog")')
            wait('!document.querySelector("[role=dialog]")')
            assert evaluate('window.__c10Policy.value===' + json.dumps(exact_to))
            click('button', text['policySave'])
            wait('Boolean(document.querySelector("[role=dialog]"))'); shot('policy', 'confirmation')
            click('button', text['cancel'], '.getByRole("dialog")')
            wait('!document.querySelector("[role=dialog]")')
            writes_before = len([row for row in fixture['requests'] if row['method'] != 'GET'])
            rows_before = policy_rows()
            click('button', text['policySave']); click('button', text['confirm'], '.getByRole("dialog")')
            wait('Array.from(document.querySelectorAll("[role=status]")).some(e=>e.innerText.includes("APV2-"))')
            settled()
            new_writes = [row for row in fixture['requests'] if row['method'] != 'GET'][writes_before:]
            assert len(new_writes) == 1
            request = new_writes[0]
            assert request['method'] == 'POST' and request['path'] == '/api/v1/orgs/test/agents/engineering_head/team-escalation-policy/v2/releases'
            body = request['body']
            assert body == {**before_policy['v2_starter'], 'team': 'engineering',
                'what_to_escalate': exact_to, 'what_not_to_escalate': exact_not,
                'based_on_selector_id': None, 'expected_selector_id': None, 'action': 'bootstrap',
                'create_request_id': body['create_request_id'], 'activation_request_id': body['activation_request_id'],
                'acknowledge_shared_credential_attribution': True}
            assert body['create_request_id'] and body['activation_request_id'] and body['create_request_id'] != body['activation_request_id']
            readback = httpx.get(_base(port) + '/agents/engineering_head/team-escalation-policy', headers=_auth_headers()).raise_for_status().json()
            assert readback['family'] == 'v2' and readback['team'] == 'engineering' and readback['selector_epoch'] == 1
            assert readback['active']['release']['what_to_escalate'] == exact_to
            assert readback['active']['release']['what_not_to_escalate'] == exact_not
            rows_after = policy_rows()
            for table in ('authority_policy_v2_releases', 'authority_policy_v2_activations'):
                assert len(rows_after[table]) == len(rows_before[table]) + 1
            # One paired control POST commits two distinct audit facts.
            assert len(rows_after['authority_policy_v2_control_audit']) == len(rows_before['authority_policy_v2_control_audit']) + 2
            with sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro', uri=True) as conn:
                row = conn.execute('SELECT what_to_escalate,what_not_to_escalate FROM authority_policy_v2_releases WHERE id=?', (readback['active']['release']['id'],)).fetchone()
                assert row == (exact_to, exact_not)
            shot('policy', 'populated-v2-independent-readback')
            action('await page.getByRole("textbox",{name:' + json.dumps(text['to']) + ',exact:true}).fill("Explicitly discarded C10 draft");await page.locator("section[aria-labelledby=team-policy-page-heading] > a").click();')
            wait('Boolean(document.querySelector("[role=dialog]"))'); shot('policy', 'dirty-navigation-discard')
            click('button', text['discard'], '.getByRole("dialog")')
            wait('location.pathname.endsWith("/agents/engineering_head")')
            assert policy_rows() == rows_after
            # No canonical roster mutation occurred at the empty UI projection.
            expected_roster = yaml.safe_load(canonical_before)
            expected_roster['teams']['default']['workers'].append('browser_worker')
            assert yaml.safe_load((root / 'org/teams.yaml').read_text()) == expected_roster
            (tmp_path / 'C10-browser-receipt.json').write_text(json.dumps({
                'source_sha': binding['revision'], 'cli': cli, 'cli_version': cli_version, 'selected_tools': selected,
                'browser_user_agent': evaluate('navigator.userAgent'), 'screenshots': evidence,
                'requests': fixture['requests'], 'worker_retention': retention, 'locale_windows': locale_windows,
                'denials': denials, 'enrollment_draft_retention': preserved_enrollment,
                'policy_request': body, 'canonical_policy_readback': readback,
                'empty_default_boundary': 'coherent transport projection; unrelated real teams populated',
                'demotion_boundary': 'Web coherent before/after cache cases only; real M proof requires actual C6 check/apply/receipt',
            }, sort_keys=True))
        finally:
            for key in ('release', 'teams_release', 'settings_release', 'policy_release'):
                fixture[key].set()
            if opened:
                pw('close')
