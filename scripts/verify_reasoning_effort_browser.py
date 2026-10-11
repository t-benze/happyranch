#!/usr/bin/env python3
"""Bounded THR298 actual API/browser save-reload checks (no response mocks).

Prerequisite: owned reasoning_effort_browser_fixture.py, ordinary Web build,
locked e2e/browser.lock installed in a local venv, Chromium executable supplied.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import httpx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def require(value, reason):
    if not value:
        raise AssertionError(reason)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--chromium', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    require(args.base_url.startswith('http://127.0.0.1:'), 'loopback-only fixture')
    root = args.root.resolve()
    require(root.name.startswith('TASK-10683-browser-venue-') and (root / 'daemon/daemon.token').is_file(), 'owned disposable fixture required')
    args.output.mkdir(parents=True, exist_ok=True)
    client = httpx.Client(base_url=args.base_url, headers={'Authorization': 'Bearer ' + (root / 'daemon/daemon.token').read_text().strip()}, timeout=10)
    prefix = '/api/v1/orgs/effort-fixture/agents/case_worker'
    agent_path = root / 'runtime/orgs/effort-fixture/org/agents/case_worker.md'
    receipts = []

    def request(method, path, body=None, expected=200):
        response = client.request(method, path, json=body)
        require(response.status_code == expected, f'{method} {path}: {response.status_code} {response.text}')
        receipts.append({'method': method, 'path': path, 'body': body, 'status': response.status_code})
        return response.json()

    def roster():
        return next(a for a in request('GET', '/api/v1/orgs/effort-fixture/agents')['agents'] if a['name'] == 'case_worker')

    # Real save endpoint for every declared builtin value, pinned model.
    from runtime.adapters import get_builtin_catalog
    for descriptor in get_builtin_catalog():
        request('PUT', prefix + '/executor', {'executor': descriptor.name})
        for effort in descriptor.reasoning_effort_values:
            request('PUT', prefix + '/model', {'model': 'provider/model', 'reasoning_effort': effort, 'expected_executor': descriptor.name})
            require(roster()['reasoning_effort'] == effort, 'real API save/reload each builtin static value')
        before = agent_path.read_bytes()
        request('PUT', prefix + '/model', {'reasoning_effort': 'undeclared-value'}, expected=422)
        require(agent_path.read_bytes() == before, 'each CLI invalid save leaves persisted bytes unchanged')
    # Contract schemas through normal scoped loopback registration token. Never emit token.
    token_response = client.post('/api/v1/auth/registration-token/runtime', json={'name':'contract-case', 'purpose':'adapter', 'intended_profile_name':'contract_case'})
    require(token_response.status_code == 200, 'synthetic scoped token mint')
    reference_response = client.get('/api/v1/runtime/adapters/contract-reference', headers={'Authorization':'Bearer ' + token_response.json()['token']})
    require(reference_response.status_code == 200, 'real contract-reference route')
    reference = reference_response.json()
    require(reference['contract_version'] == 1 and reference['input_v2_schema']['properties']['contract_version']['const'] == 2 and reference['input_options_schema']['properties']['version']['const'] == 2, 'default/output v1 + explicit input2 reference schemas')
    receipts.append({'case':'D-reference', 'status':200, 'legacy_version':1, 'input_version':2})

    # Exact before/after persistence refusals, real route (not mocked UI).
    request('PUT', prefix + '/executor', {'executor': 'claude'})
    request('PUT', prefix + '/model', {'model': 'pinned-model', 'reasoning_effort': 'high', 'expected_executor': 'claude'})
    selected = roster()
    require(selected['model'] == 'pinned-model' and selected['reasoning_effort'] == 'high', 'selection persistence')
    original = agent_path.read_bytes()
    for bad in (True, 1, [], {}, '', 'invalid', 'HIGH'):
        request('PUT', prefix + '/model', {'reasoning_effort': bad, 'expected_executor': 'claude'}, expected=422)
        require(agent_path.read_bytes() == original, 'invalid save must leave canonical bytes unchanged')
    request('PUT', prefix + '/model', {'expected_executor': 'codex', 'reasoning_effort': 'high'}, expected=409)
    require(agent_path.read_bytes() == original, 'stale CLI must refuse before mutation')
    request('PUT', prefix + '/model', {})
    require(agent_path.read_bytes() == original, 'omitted update preserves selection')
    request('POST', prefix + '/repos', {'action': 'add', 'repo_name': 'bounded-source', 'url': str(root / 'synthetic-source.git')})
    require(roster()['reasoning_effort'] == 'high', 'unrelated whole-definition repo writer preserves effort')
    request('POST', prefix + '/repos', {'action': 'remove', 'repo_name': 'bounded-source'})
    require(roster()['reasoning_effort'] == 'high', 'remove repo preserves effort')
    # Prompt writer uses ordinary expected-revision route, preserving the same field.
    from runtime.orchestrator.agent_def import parse_agent_text
    row = {'system_prompt': parse_agent_text(agent_path.read_text(), expected_name='case_worker').system_prompt, 'revision': hashlib.sha256(agent_path.read_bytes()).hexdigest()}
    request('PUT', prefix + '/system-prompt', {'system_prompt': row['system_prompt'] + '\nSynthetic bounded preservation check.\n', 'expected_revision': row['revision']})
    require(roster()['reasoning_effort'] == 'high', 'prompt writer preserves effort')
    request('PUT', prefix + '/model', {'reasoning_effort': None})
    require(roster()['reasoning_effort'] is None and roster()['model'] == 'pinned-model', 'explicit clear/reload preserves model')
    request('PUT', prefix + '/executor', {'executor': 'legacy_custom'})
    before = agent_path.read_bytes()
    request('PUT', prefix + '/model', {'reasoning_effort': 'high'}, expected=422)
    require(agent_path.read_bytes() == before, 'unsupported legacy custom effort refusal')
    request('PUT', prefix + '/executor', {'executor': 'opencode'})
    before = agent_path.read_bytes()
    request('PUT', prefix + '/model', {'reasoning_effort': 'high'}, expected=422)
    require(agent_path.read_bytes() == before, 'OpenCode default model cannot silently drop effort')
    request('PUT', prefix + '/model', {'model': 'provider/model#high', 'reasoning_effort': 'high'})
    before = agent_path.read_bytes()
    request('PUT', prefix + '/model', {'reasoning_effort': 'low'}, expected=422)
    require(agent_path.read_bytes() == before, 'conflicting OpenCode variant refusal before persistence')
    request('PUT', prefix + '/executor', {'executor': 'pi'})
    request('PUT', prefix + '/model', {'model': 'provider/model:high', 'reasoning_effort': 'high'})
    before = agent_path.read_bytes()
    request('PUT', prefix + '/model', {'reasoning_effort': 'low'}, expected=422)
    require(agent_path.read_bytes() == before, 'conflicting Pi suffix refusal before persistence')
    request('PUT', prefix + '/executor', {'executor': 'claude'})

    # Read-only actual retained publication rows, which later UI writes must preserve.
    db_path = root / 'runtime/orgs/effort-fixture/db/org.db'
    # OrgPaths is the shipping path owner; never guess a DB file / create one.
    from runtime.orchestrator._paths import OrgPaths
    db_path = OrgPaths(root=root / 'runtime/orgs/effort-fixture').db_path
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as connection:
        historical = {row[0]: bytes(row[1]) for row in connection.execute('SELECT id,snapshot_bytes FROM workflow_publication_journals')}
    browser_events = []
    screenshots = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium)
        for width, height in ((390, 844), (1440, 900)):
            context = browser.new_context(viewport={'width': width, 'height': height}, locale='en-US')
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('request', lambda r: browser_events.append({'event': 'request', 'method': r.method, 'path': r.url.split(args.base_url)[-1]}) if r.url.startswith(args.base_url) and r.method != 'GET' else None)
            page.on('pageerror', lambda error: browser_events.append({'event': 'pageerror', 'message': str(error)}))

            def open_agent(locale='en'):
                page.add_init_script(f"localStorage.setItem('happyranch.ui.locale', {json.dumps(locale)})")
                page.goto(args.base_url + '/orgs/effort-fixture/agents/case_worker')
                expect(page.locator('#agent-reasoning-effort')).to_be_visible()

            def save():
                before = len(browser_events)
                with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.request.method == 'PUT') as result:
                    page.get_by_role('button', name='Save agent', exact=True).click()
                require(result.value.status == 200, 'UI save must persist through actual route')
                expect(page.get_by_role('button', name='Save agent', exact=True)).not_to_be_visible()
                require(len(browser_events) > before, 'actual save request observed')

            def snapshot(state, locale):
                # Locale transition uses the real browser adapter; no reload or API mutation.
                mutation_count = len(browser_events)
                page.evaluate('''locale => { localStorage.setItem('happyranch.ui.locale', locale); window.dispatchEvent(new StorageEvent('storage', {key:'happyranch.ui.locale', newValue:locale, storageArea:localStorage})); }''', locale)
                expect(page.locator('html')).to_have_attribute('lang', locale)
                page.locator('#agent-reasoning-effort').scroll_into_view_if_needed()
                target = args.output / f'effort-{width}x{height}-{locale}-{state}.png'
                page.screenshot(path=str(target))
                require(len(browser_events) == mutation_count, 'locale change/screenshot must not mutate API')
                screenshots.append({'state': state, 'locale': locale, 'width': width, 'height': height, 'file': target.name})

            request('PUT', prefix + '/executor', {'executor': 'claude'})
            request('PUT', prefix + '/model', {'model': None, 'reasoning_effort': None})
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('')
            for locale in ('en', 'zh-CN'):
                snapshot('default', locale)
            open_agent()
            page.get_by_label('Model', exact=True).fill('pinned-model')
            page.locator('#agent-reasoning-effort').select_option('high')
            # Draft + focus survive locale switch without remount/mutation.
            page.get_by_label('Model', exact=True).focus()
            node = page.get_by_label('Model', exact=True).element_handle()
            mutations = len(browser_events)
            page.evaluate("localStorage.setItem('happyranch.ui.locale','zh-CN'); window.dispatchEvent(new StorageEvent('storage',{key:'happyranch.ui.locale',newValue:'zh-CN',storageArea:localStorage}));")
            expect(page.get_by_label('模型', exact=True)).to_have_value('pinned-model')
            require(node.evaluate('(node) => node === document.activeElement'), 'locale switch must preserve focused input node')
            expect(page.locator('#agent-reasoning-effort')).to_have_value('high')
            require(len(browser_events) == mutations, 'locale switching draft no extra mutations')
            page.evaluate("window.dispatchEvent(new StorageEvent('storage',{key:'happyranch.ui.locale',newValue:'en',storageArea:localStorage}));")
            save()
            page.reload()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('high')
            for locale in ('en', 'zh-CN'):
                snapshot('selected', locale)
            open_agent()
            page.locator('#agent-reasoning-effort').select_option('')
            save()
            page.reload()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('')
            require(roster()['model'] == 'pinned-model' and roster()['reasoning_effort'] is None, 'UI clear reloads unset')
            # Stale draft remains bound to the CLI for which it was authored.
            page.locator('#agent-reasoning-effort').select_option('high')
            request('PUT', prefix + '/executor', {'executor': 'pi'})
            with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 409):
                page.get_by_role('button', name='Save agent', exact=True).click()
            require(roster()['reasoning_effort'] is None, 'stale UI cannot save old CLI effort')
            request('PUT', prefix + '/executor', {'executor': 'claude'})
            request('PUT', prefix + '/model', {'model': 'old-cli-model', 'reasoning_effort': 'high'})
            open_agent()
            page.get_by_label('Executor', exact=True).select_option('pi')
            expect(page.locator('#agent-reasoning-effort')).to_have_value('')
            expect(page.get_by_label('Model', exact=True)).to_have_value('')
            with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 200):
                page.get_by_role('button', name='Save agent', exact=True).click()
            require(roster()['executor'] == 'pi' and roster()['model'] is None and roster()['reasoning_effort'] is None, 'CLI switch clears saved model/effort')
            request('PUT', prefix + '/executor', {'executor': 'legacy_custom'})
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_be_disabled()
            for locale in ('en', 'zh-CN'):
                snapshot('unsupported', locale)
            request('PUT', prefix + '/executor', {'executor': 'opencode'})
            open_agent()
            page.locator('#agent-reasoning-effort').select_option('high')
            with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 422):
                page.get_by_role('button', name='Save agent', exact=True).click()
            for locale in ('en', 'zh-CN'):
                snapshot('error', locale)
            require(roster()['model'] is None and roster()['reasoning_effort'] is None, 'failed UI save retains canonical state')
            context.close()
        require(not any(event['event'] == 'pageerror' for event in browser_events), 'no JS exceptions')
        browser.close()
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as connection:
        after = {row[0]: bytes(row[1]) for row in connection.execute('SELECT id,snapshot_bytes FROM workflow_publication_journals')}
    require(all(after.get(key) == value for key, value in historical.items()), 'actual retained publication history stays byte-identical')
    receipts.append({'case': 'E-retained-history', 'count': len(historical), 'sha256': {key: hashlib.sha256(value).hexdigest() for key, value in historical.items()}})
    (args.output / 'browser-receipts.json').write_text(json.dumps({'api': receipts, 'browser': browser_events, 'screenshots': screenshots}, indent=2) + '\n')
    print(f'PASS real API/browser persistence, {len(screenshots)} bilingual screenshots; lifecycle workers never started')


if __name__ == '__main__':
    main()
