#!/usr/bin/env python3
"""Bounded THR298 actual API/browser save-reload checks (no response mocks).

Prerequisite: owned reasoning_effort_browser_fixture.py, ordinary Web build,
locked e2e/browser.lock installed in a local venv, Chromium executable supplied.
"""
from __future__ import annotations

import argparse
import atexit
import base64
import dataclasses
import os
import subprocess
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
    handoff = []
    browser_events = []
    screenshots = []
    def retain_prefix():
        (args.output / 'browser-prefix-receipts.json').write_text(json.dumps({'api': receipts, 'browser': browser_events, 'screenshots': screenshots, 'status': 'prefix-only; inspect command exit for completion'}, indent=2) + '\n')
    atexit.register(retain_prefix)

    def export_ui(label):
        from runtime.orchestrator.agent_def import parse_agent_text
        raw = agent_path.read_bytes()
        agent = parse_agent_text(raw.decode(), expected_name="case_worker")
        row = {"source_case": label, "cli": agent.executor, "model": agent.model, "effort": agent.reasoning_effort,
               "bytes_base64": base64.b64encode(raw).decode(), "sha256": hashlib.sha256(raw).hexdigest()}
        handoff.append(row)
        (args.output / "ui-canonical-handoff.json").write_text(json.dumps(handoff, indent=2) + "\n")

    def request(method, path, body=None, expected=200):
        before = agent_path.read_bytes() if agent_path.exists() else b''
        response = client.request(method, path, json=body)
        receipts.append({'method': method, 'path': path, 'body': body, 'status': response.status_code, 'response': response.json(), 'canonical_before_base64': base64.b64encode(before).decode(), 'canonical_after_base64': base64.b64encode(agent_path.read_bytes()).decode()})
        (args.output / 'api-prefix-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
        require(response.status_code == expected, f'{method} {path}: {response.status_code} {response.text}')

        return response.json()

    def roster():
        return next(a for a in request('GET', '/api/v1/orgs/effort-fixture/agents')['agents'] if a['name'] == 'case_worker')

    # Real save endpoint: retained ordinary values plus safe novel/boundary tokens.
    from runtime.adapters import get_builtin_catalog
    for descriptor in get_builtin_catalog():
        request('PUT', prefix + '/executor', {'executor': descriptor.name})
        for effort in (*descriptor.reasoning_effort_values, 'future_Tier-2', 'HIGH', 'A', 'A' + 'a' * 31, 'none'):
            request('PUT', prefix + '/model', {'model': 'provider/model', 'reasoning_effort': effort, 'expected_executor': descriptor.name})
            require(roster()['reasoning_effort'] == effort, 'real API save/reload each builtin static value')
        before = agent_path.read_bytes()
        request('PUT', prefix + '/model', {'reasoning_effort': 'h=igh'}, expected=422)
        require(agent_path.read_bytes() == before, 'each CLI invalid save leaves persisted bytes unchanged')
    # B1/B2-create: actual Founder writer, no manager/session enrollment setup.
    import yaml
    from runtime.orchestrator._paths import OrgPaths
    from runtime.orchestrator.agent_def import parse_agent_text
    paths = OrgPaths(root=root / 'runtime/orgs/effort-fixture')
    isolation = json.loads((root / 'isolation.json').read_text())
    require(Path(isolation['workspaces']).resolve() == paths.workspaces_dir.resolve(), 'resolved bootstrap workspace owner')
    for destination in (paths.workspaces_dir, root / 'daemon', root / 'runtime', *[Path(p) for p in isolation['homes'].values()]):
        require(destination.resolve().is_relative_to(root), 'all writable bootstrap destinations must be private')
    teams_path = paths.org_dir / 'teams.yaml'
    for cli in ('claude', 'codex', 'pi', 'opencode'):
        for case in ('ok', 'malformed', 'long', 'type') + (('conflict',) if cli == 'opencode' else ()):
            name = f'effort_create_{cli}_{case}'
            candidate = paths.agents_dir / (name + '.md')
            pending = paths.pending_agents_dir / (name + '.md')
            archived = paths.agents_dir / '_terminated' / (name + '.md')
            workspace = paths.workspaces_dir / name
            team_before = teams_path.read_bytes()
            require(not any(p.exists() for p in (candidate, pending, archived, workspace)) and name not in team_before.decode(), 'fresh candidate definition/membership/workspace')
            body = {'name': name, 'role': 'worker', 'team': 'engineering', 'executor': cli, 'model': 'provider/model', 'reasoning_effort': 'future_Tier-2', 'description': 'Disposable effort writer case', 'system_prompt': 'Synthetic fixture worker; no invocation.', 'repos': {}, 'allow_rules': []}
            if case == 'malformed': body['reasoning_effort'] = 'h=igh'
            if case == 'long': body['reasoning_effort'] = 'a' * 33
            if case == 'type': body['reasoning_effort'] = True
            if case == 'conflict': body['model'] = 'provider/model#other'
            response = client.post('/api/v1/orgs/effort-fixture/agents', json=body)
            record = {'case': 'B1-create' if case == 'ok' else 'B2-create', 'cli': cli, 'variant': case, 'body': body, 'path': '/api/v1/orgs/effort-fixture/agents', 'status': response.status_code, 'response': response.text, 'teams_before_base64': base64.b64encode(team_before).decode(), 'teams_after_base64': base64.b64encode(teams_path.read_bytes()).decode(), 'paths': {str(p.relative_to(root)): p.exists() for p in (candidate, pending, archived, workspace)}, 'canonical_base64': base64.b64encode(candidate.read_bytes()).decode() if candidate.exists() else None}
            if candidate.exists(): record['canonical_sha256'] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            receipts.append(record)
            (args.output / 'api-prefix-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
            require(response.status_code == (200 if case == 'ok' else 422), 'create positive/refusal status; bootstrap failure residue is retained in receipt')
            if case == 'ok':
                require(response.json() == {'name': name, 'team': 'engineering', 'role': 'worker'}, 'create exact response identity/team/role')
                definition = parse_agent_text(candidate.read_text(), expected_name=name)
                require((definition.executor, definition.model, definition.reasoning_effort) == (cli, 'provider/model', 'future_Tier-2'), 'create canonical tuple')
                workers = yaml.safe_load(teams_path.read_text())['teams']['engineering']['workers']
                require(workers.count(name) == 1 and workspace.is_dir() and not pending.exists(), 'create exact membership and ordinary workspace bootstrap')
                row = next(a for a in request('GET', '/api/v1/orgs/effort-fixture/agents')['agents'] if a['name'] == name)
                require(row['model'] == 'provider/model' and row['reasoning_effort'] == 'future_Tier-2', 'create exact roster reload')
            else:
                require(not any(p.exists() for p in (candidate, pending, archived, workspace)) and teams_path.read_bytes() == team_before, 'create refusal leaves no candidate residue')
                if case == 'type':
                    require(any(e['loc'] == ['body', 'reasoning_effort'] for e in response.json()['detail']), 'request-model refusal attribution')
                else:
                    require(response.json()['detail']['code'] == 'invalid_reasoning_effort', 'route-validator refusal attribution')

    # C2/B2 public CLI: literal equals-form strings; no NUL/type argv claims.
    cli_env = dict(os.environ)
    cli_env['HAPPYRANCH_DAEMON_HOME'] = str(root / 'daemon')
    cli_env['HAPPYRANCH_RUNTIME_ROOT'] = str(root / 'runtime')
    for key, path in isolation['homes'].items(): cli_env[key] = path
    for key in tuple(cli_env):
        if key.startswith(('ANTHROPIC_', 'OPENAI_', 'CODEX_', 'CLAUDE_', 'PI_', 'OPENCODE_', 'GEMINI_', 'GOOGLE_API_')): cli_env.pop(key)
    log_path = root / 'selection-requests.jsonl'
    for cli in ('claude', 'codex', 'pi', 'opencode'):
        command = [sys.executable, '-m', 'cli.main', 'set-executor', '--org', 'effort-fixture', 'case_worker', '--executor', cli]
        result = subprocess.run(command, env=cli_env, capture_output=True, text=True, timeout=20)
        require(result.returncode == 0, 'public set-executor CLI')
        for value in ('future_Tier-2', 'none', '', 'a' * 33, '-high', '_high', '1high', 'h.igh', 'h/igh', 'h=igh', "h'igh", 'h"igh', 'h\\igh', 'h$(x)', 'h`x`', 'h{x}', 'hi gh', ' high', 'high ', '  ', 'hi\tgh', 'hi\ngh', 'hi\rgh', 'hi\x01gh', 'é', 'hi;gh', 'hi#gh', 'hi:gh'):
            request('PUT', prefix + '/model', {'model': 'provider/model', 'reasoning_effort': 'high', 'expected_executor': cli})
            before = agent_path.read_bytes()
            observed_count = len(log_path.read_text().splitlines())
            command = [sys.executable, '-m', 'cli.main', 'set-model', '--org', 'effort-fixture', 'case_worker', '--reasoning-effort=' + value]
            result = subprocess.run(command, env=cli_env, capture_output=True, text=True, timeout=20)
            observed_requests = [json.loads(line) for line in log_path.read_text().splitlines()[observed_count:]]
            accepted = value in ('future_Tier-2', 'none')
            receipts.append({'case': 'B2/C2-public-CLI', 'argv': command, 'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr, 'requests': observed_requests, 'before_base64': base64.b64encode(before).decode(), 'after_base64': base64.b64encode(agent_path.read_bytes()).decode()})
            (args.output / 'api-prefix-receipts.json').write_text(json.dumps(receipts, indent=2) + '\n')
            require(result.returncode == (0 if accepted else 1), 'public CLI expected status')
            require(any(r['path'] == prefix + '/model' and r['status'] == (200 if accepted else 422) for r in observed_requests), 'actual backend request observed, not parser/caller refusal')
            if accepted: require(roster()['reasoning_effort'] == value, 'CLI case-preserving saved token')
            else: require(agent_path.read_bytes() == before, 'CLI refusal exact canonical bytes')
        command = [sys.executable, '-m', 'cli.main', 'set-model', '--org', 'effort-fixture', 'case_worker', '--clear-reasoning-effort']
        result = subprocess.run(command, env=cli_env, capture_output=True, text=True, timeout=20)
        require(result.returncode == 0 and roster()['reasoning_effort'] is None, 'public CLI explicit clear')
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
    request('PUT', prefix + '/model', {'model': 'pinned-model', 'reasoning_effort': 'future_Tier-2', 'expected_executor': 'claude'})
    selected = roster()
    require(selected['model'] == 'pinned-model' and selected['reasoning_effort'] == 'future_Tier-2', 'selection persistence')
    original = agent_path.read_bytes()
    for bad in (True, 7, 1.5, [], {}, '', 'a' * 33, '-high', '_high', '1high', 'h.igh', 'h/igh', 'h=igh', "h'igh", 'h"igh', 'h\\igh', 'h$(x)', 'h`x`', 'h{x}', 'hi gh', ' high', 'high ', '  ', 'hi\tgh', 'hi\ngh', 'hi\rgh', 'hi\x01gh', 'é', 'hi;gh', 'hi#gh', 'hi:gh', 'hi\x00gh'):
        request('PUT', prefix + '/model', {'reasoning_effort': bad, 'expected_executor': 'claude'}, expected=422)
        require(agent_path.read_bytes() == original, 'invalid save must leave canonical bytes unchanged')
    request('PUT', prefix + '/model', {'expected_executor': 'codex', 'reasoning_effort': 'future_Tier-2'}, expected=409)
    require(agent_path.read_bytes() == original, 'stale CLI must refuse before mutation')
    request('PUT', prefix + '/executor', {'executor': 'claude'})
    require(agent_path.read_bytes() == original, 'same SetExecutor keeps canonical selection')
    request('PUT', prefix + '/model', {'model': 'updated-model'})
    require(roster()['reasoning_effort'] == 'future_Tier-2', 'model-only setter preserves novel effort')
    request('PUT', prefix + '/model', {'model': 'pinned-model'})
    original = agent_path.read_bytes()
    request('PUT', prefix + '/model', {})
    require(agent_path.read_bytes() == original, 'omitted update preserves selection')
    request('POST', prefix + '/repos', {'action': 'add', 'repo_name': 'bounded-source', 'url': str(root / 'synthetic-source.git')})
    require(roster()['reasoning_effort'] == 'future_Tier-2', 'unrelated whole-definition repo writer preserves effort')
    request('POST', prefix + '/repos', {'action': 'remove', 'repo_name': 'bounded-source'})
    require(roster()['reasoning_effort'] == 'future_Tier-2', 'remove repo preserves effort')
    # Prompt writer uses ordinary expected-revision route, preserving the same field.
    from runtime.orchestrator.agent_def import parse_agent_text
    row = {'system_prompt': parse_agent_text(agent_path.read_text(), expected_name='case_worker').system_prompt, 'revision': hashlib.sha256(agent_path.read_bytes()).hexdigest()}
    request('PUT', prefix + '/system-prompt', {'system_prompt': row['system_prompt'] + '\nSynthetic bounded preservation check.\n', 'expected_revision': row['revision']})
    require(roster()['reasoning_effort'] == 'future_Tier-2', 'prompt writer preserves effort')
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
    # B3/B4 actual paired setter composition acceptance/refusal, exact bytes.
    for cli in ('opencode', 'pi'):
        request('PUT', prefix + '/executor', {'executor': cli})
        pairs = [('provider/org/model', 'future_Tier-2', 200), ('provider/model#future_Tier-2', 'future_Tier-2', 200), ('provider/model#high', None, 200), (None, 'future_Tier-2', 422), ('provider/model#other', 'future_Tier-2', 422), ('provider/model#future_tier-2', 'future_Tier-2', 422), ('provider//model', 'high', 422), ('provider/model#', 'high', 422), ('provider/model#high#high', 'high', 422), ('provider model/id', 'high', 422), ('p/m\x00', 'high', 422)] if cli == 'opencode' else [('provider/model:high', None, 200), ('provider/model:high', 'high', 200), ('provider/model:high', 'future_Tier-2', 422), ('provider/model:ordinary', 'future_Tier-2', 200), ('provider/model:future_Tier-2', 'future_Tier-2', 200)]
        for model, effort, status in pairs:
            before = agent_path.read_bytes()
            request('PUT', prefix + '/model', {'model': model, 'reasoning_effort': effort, 'expected_executor': cli}, expected=status)
            if status == 422: require(agent_path.read_bytes() == before, 'composition setter refusal exact bytes')
    request('PUT', prefix + '/executor', {'executor': 'claude'})

    # Read-only actual retained publication rows, which later UI writes must preserve.
    db_path = root / 'runtime/orgs/effort-fixture/db/org.db'
    # OrgPaths is the shipping path owner; never guess a DB file / create one.
    from runtime.orchestrator._paths import OrgPaths
    db_path = OrgPaths(root=root / 'runtime/orgs/effort-fixture').db_path
    with sqlite3.connect(f'file:{db_path}?mode=ro', uri=True) as connection:
        historical = {row[0]: bytes(row[1]) for row in connection.execute('SELECT id,snapshot_bytes FROM workflow_publication_journals')}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=args.chromium)
        for width, height in ((390, 844), (1440, 900)):
            context = browser.new_context(viewport={'width': width, 'height': height}, locale='en-US')
            page = context.new_page()
            page.set_default_timeout(10000)
            page.on('request', lambda r: browser_events.append({'event': 'request', 'method': r.method, 'path': r.url.split(args.base_url)[-1], 'body': r.post_data}) if r.url.startswith(args.base_url) else None)
            page.on('response', lambda r: browser_events.append({'event': 'response', 'method': r.request.method, 'path': r.url.split(args.base_url)[-1], 'status': r.status}) if r.url.startswith(args.base_url) else None)
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
                export_ui(f'{width}x{height}-UI-save')

            def snapshot(state, locale):
                # Locale transition uses the real browser adapter; no reload or API mutation.
                mutation_count = sum(e['event'] == 'request' for e in browser_events)
                page.evaluate('''locale => { localStorage.setItem('happyranch.ui.locale', locale); window.dispatchEvent(new StorageEvent('storage', {key:'happyranch.ui.locale', newValue:locale, storageArea:localStorage})); }''', locale)
                expect(page.locator('html')).to_have_attribute('lang', locale)
                page.locator('#agent-reasoning-effort').scroll_into_view_if_needed()
                target = args.output / f'effort-{width}x{height}-{locale}-{state}.png'
                page.screenshot(path=str(target))
                require(sum(e['event'] == 'request' for e in browser_events) == mutation_count, 'locale change/screenshot must not trigger finite reads or mutations')
                screenshots.append({'state': state, 'locale': locale, 'width': width, 'height': height, 'file': target.name, 'sha256': hashlib.sha256(target.read_bytes()).hexdigest()})
                (args.output / 'screenshots.json').write_text(json.dumps(screenshots, indent=2) + '\n')

            request('PUT', prefix + '/executor', {'executor': 'claude'})
            request('PUT', prefix + '/model', {'model': None, 'reasoning_effort': None})
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('')
            for locale in ('en', 'zh-CN'):
                snapshot('default', locale)
            open_agent()
            page.get_by_label('Model', exact=True).fill('pinned-model')
            page.locator('#agent-reasoning-effort').fill('future_Tier-2')
            # Draft + focus survive locale switch without remount/mutation.
            page.get_by_label('Model', exact=True).focus()
            node = page.get_by_label('Model', exact=True).element_handle()
            mutations = sum(e['event'] == 'request' for e in browser_events)
            page.evaluate("localStorage.setItem('happyranch.ui.locale','zh-CN'); window.dispatchEvent(new StorageEvent('storage',{key:'happyranch.ui.locale',newValue:'zh-CN',storageArea:localStorage}));")
            expect(page.get_by_label('模型', exact=True)).to_have_value('pinned-model')
            require(node.evaluate('(node) => node === document.activeElement'), 'locale switch must preserve focused input node')
            expect(page.locator('#agent-reasoning-effort')).to_have_value('future_Tier-2')
            require(sum(e['event'] == 'request' for e in browser_events) == mutations, 'locale switching draft no extra mutations')
            page.evaluate("window.dispatchEvent(new StorageEvent('storage',{key:'happyranch.ui.locale',newValue:'en',storageArea:localStorage}));")
            save()
            page.reload()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('future_Tier-2')
            for locale in ('en', 'zh-CN'):
                snapshot('selected', locale)
            open_agent()
            page.locator('#agent-reasoning-effort').fill('')
            save()
            page.reload()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('')
            require(roster()['model'] == 'pinned-model' and roster()['reasoning_effort'] is None, 'UI clear reloads unset')
            # A/B: actual SPA save/reload/edit/clear for every builtin, exporting
            # the canonical bytes actually written by the UI for process evidence.
            for cli in ('claude', 'codex', 'pi', 'opencode'):
                request('PUT', prefix + '/executor', {'executor': cli})
                request('PUT', prefix + '/model', {'model': 'provider/model', 'reasoning_effort': 'high'})
                open_agent()
                expect(page.locator('#agent-reasoning-effort')).to_have_value('high')
                for value in ('future_Tier-2', 'none', ''):
                    page.locator('#agent-reasoning-effort').fill(value)
                    save()
                    page.reload()
                    expect(page.locator('#agent-reasoning-effort')).to_have_value(value)
                    require(roster()['reasoning_effort'] == (value or None), 'UI-save exact token/default roster')
            request('PUT', prefix + '/executor', {'executor': 'claude'})
            open_agent()
            # Actual mounted effort caret/draft/actions survive both locale changes.
            effort = page.locator('#agent-reasoning-effort')
            effort.fill('future_Tier-2')
            effort.focus()
            effort.evaluate('(node) => node.setSelectionRange(2, 7)')
            node = effort.element_handle()
            for locale in ('zh-CN', 'en'):
                count = sum(e['event'] == 'request' for e in browser_events)
                page.evaluate("locale => window.dispatchEvent(new StorageEvent('storage',{key:'happyranch.ui.locale',newValue:locale,storageArea:localStorage}))", locale)
                require(node.evaluate('(node) => node === document.activeElement && node.selectionStart === 2 && node.selectionEnd === 7 && node.value === "future_Tier-2"'), 'effort node/focus/caret/draft preserved')
                require(sum(e['event'] == 'request' for e in browser_events) == count, 'locale effort draft no finite reads/mutations')
                require(page.get_by_role('button', name='Save agent' if locale == 'en' else '保存智能体', exact=True).is_enabled(), 'original save action preserved')
            receipts.append({'case': 'C3-caret', 'width': width, 'node_focus_and_selection': [2, 7], 'draft': 'future_Tier-2', 'locale_finite_requests': 0})
            # Stale draft remains bound to the CLI for which it was authored.
            page.locator('#agent-reasoning-effort').fill('future_Tier-2')
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
            request('PUT', prefix + '/executor', {'executor': 'claude'})
            request('PUT', prefix + '/model', {'model': None, 'reasoning_effort': None})
            open_agent()
            page.locator('#agent-reasoning-effort').fill('h=igh')
            with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 422):
                page.get_by_role('button', name='Save agent', exact=True).click()
            for locale in ('en', 'zh-CN'):
                snapshot('error', locale)
            require(roster()['model'] is None and roster()['reasoning_effort'] is None, 'failed UI save retains canonical state')
            expect(page.locator('#agent-reasoning-effort')).to_have_value('h=igh')
            for locale in ('en', 'zh-CN'):
                snapshot('malformed-draft', locale)
            request('PUT', prefix + '/executor', {'executor': 'opencode'})
            request('PUT', prefix + '/model', {'model': None, 'reasoning_effort': None})
            open_agent()
            page.locator('#agent-reasoning-effort').fill('future_Tier-2')
            with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 422):
                page.get_by_role('button', name='Save agent', exact=True).click()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('future_Tier-2')
            for locale in ('en', 'zh-CN'):
                snapshot('composition-error', locale)
            # D1/D2: ordinary approved declaration still governs the same text input.
            request('PUT', prefix + '/executor', {'executor': 'opted_custom'})
            request('PUT', prefix + '/model', {'model': 'actual-model', 'reasoning_effort': None})
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_be_enabled()
            for value in ('high', 'low', ''):
                page.locator('#agent-reasoning-effort').fill(value)
                save()
                page.reload()
                expect(page.locator('#agent-reasoning-effort')).to_have_value(value)
            for value in ('future_Tier-2', 'HIGH'):
                original = agent_path.read_bytes()
                page.locator('#agent-reasoning-effort').fill(value)
                with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 422):
                    page.get_by_role('button', name='Save agent', exact=True).click()
                expect(page.locator('#agent-reasoning-effort')).to_have_value(value)
                require(agent_path.read_bytes() == original, 'custom undeclared value exact saved-byte refusal')
                for locale in ('en', 'zh-CN'):
                    snapshot('custom-declaration-error-' + value, locale)
                open_agent()
            request('PUT', prefix + '/executor', {'executor': 'model_only_custom'})
            request('PUT', prefix + '/model', {'model': 'actual-model'})
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_be_disabled()
            for locale in ('en', 'zh-CN'):
                snapshot('model-only-disabled', locale)
            # Historical malformed readable value is setup, not a setter success.
            request('PUT', prefix + '/executor', {'executor': 'claude'})
            definition = parse_agent_text(agent_path.read_text(), expected_name='case_worker')
            from runtime.orchestrator.agent_def import render_agent_text
            agent_path.write_text(render_agent_text(dataclasses.replace(definition, reasoning_effort='h=igh')))
            malformed_before = agent_path.read_bytes()
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('h=igh')
            page.locator('#agent-reasoning-effort').fill('')
            save()
            page.reload()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('')
            require(roster()['reasoning_effort'] is None, 'saved malformed readable value clear')
            # Stale registered-name absence is synthetic read setup only.
            current = parse_agent_text(agent_path.read_text(), expected_name='case_worker')
            agent_path.write_text(render_agent_text(dataclasses.replace(current, executor='stale_custom', reasoning_effort='future_Tier-2')))
            open_agent()
            expect(page.locator('#agent-reasoning-effort')).to_have_value('future_Tier-2')
            expect(page.locator('#agent-reasoning-effort')).to_be_disabled()
            receipts.append({'case': 'C3-stale-profile', 'saved_value': 'future_Tier-2', 'enabled': False})
            agent_path.write_text(render_agent_text(current))
            open_agent()
            receipts.append({'case': 'A1-saved-malformed-clear', 'before_base64': base64.b64encode(malformed_before).decode(), 'after_base64': base64.b64encode(agent_path.read_bytes()).decode()})
            before = agent_path.read_bytes()
            page.locator('#agent-reasoning-effort').fill('a' * 33)
            expect(page.locator('#agent-reasoning-effort')).to_have_value('a' * 33)
            with page.expect_response(lambda r: r.url.endswith('/agents/case_worker/model') and r.status == 422):
                page.get_by_role('button', name='Save agent', exact=True).click()
            require(agent_path.read_bytes() == before, 'overlength UI value refuses without truncation or mutation')
            # Browser sanitation is recorded separately from backend rejection.
            page.locator('#agent-reasoning-effort').fill('hi\ngh')
            actual = page.locator('#agent-reasoning-effort').input_value()
            require(actual == 'high', 'single-line browser newline sanitation')
            receipts.append({'case': 'B2-browser-sanitation', 'attempted': 'hi\ngh', 'actual_dom_value': actual, 'backend_refusal_claim': False})
            open_agent()
            # C3 availability via bounded browser HTTP delivery, not fake product
            # support; canonical saved text remains visible when metadata fails.
            for state in ('error', 'empty', 'loading'):
                def deliver(route, state=state):
                    if state == 'error': route.fulfill(status=503, body='unavailable')
                    elif state == 'empty': route.fulfill(status=200, content_type='application/json', body='{"prereqs": []}')
                    else: pending_routes.append(route)
                pending_routes = []
                page.route('**/api/v1/health/prereqs', deliver)
                open_agent()
                expect(page.locator('#agent-reasoning-effort')).to_be_disabled()
                receipts.append({'case': 'C3-availability', 'state': state, 'saved_value': page.locator('#agent-reasoning-effort').input_value(), 'enabled': False})
                for route in pending_routes: route.abort()
                page.unroute('**/api/v1/health/prereqs', deliver)
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
