"""Finite ordinary-distribution browser stage for the exact hosted evidence push.

Only the existing fixed native observer is elevated. No product imports here;
the genuine daemon imports its authenticated installed wheel as ordinary UID.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time


def file_receipt(path):
    path = Path(path).resolve(strict=True)
    assert path.is_file() and not path.stat().st_mode & 0o022
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def browser_stage(commands, candidate, root, uv, python, constraints, descriptor, api):
    """Build only the candidate wheel and ordinary web; prior cases are retained."""
    assert os.getuid() == os.geteuid() != 0
    save = api['save']
    node = Path(shutil.which('node')).resolve(strict=True)
    npm = node.parent / 'npm'
    assert npm.is_file()
    env = api['clean_env'](root / 'browser-build-env')
    env['PATH'] = str(node.parent) + ':' + str(uv.parent) + ':' + env['PATH']
    version, _ = commands.run('browser-node-version', [node, '--version'], root, env)
    assert version.strip() == 'v24.19.0'
    npm_version, _ = commands.run('browser-npm-version', [npm, '--version'], root, env)
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
                  if sys.platform == 'darwin' else '/opt/google/chrome/chrome')
    assert chrome.is_file(), 'fixed hosted native Chrome executable unavailable'
    chrome_version, _ = commands.run('browser-chrome-version', [chrome, '--version'], root, env)
    save('browser-tools.json', {'node': file_receipt(node), 'node_version': version.strip(),
         'npm': file_receipt(npm), 'npm_version': npm_version.strip(),
         'chrome': file_receipt(chrome), 'chrome_version': chrome_version.strip(),
         'workload_uid': os.getuid(), 'observer_uid': 0})
    build = root / 'browser-web-source'
    build.mkdir(mode=0o700)
    source_record = api['receipts'] / 'candidate-after-source-manifest.json'
    source = json.loads(source_record.read_text())
    copied = {}
    for name, sha in source['files'].items():
        if not name.startswith('web/'):
            continue
        assert name not in source['links'], 'web build external link refused'
        target = build / name.removeprefix('web/')
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        shutil.copy2(candidate / name, target)
        assert file_receipt(target)['sha256'] == sha
        copied[name] = sha
    save('browser-web-source.json', {'candidate': source['candidate_sha'], 'files': copied,
         'ordinary_build': True, 'instrumentation_flags': [], 'mock_backend': False})
    commands.run('browser-npm-ci', [npm, 'ci', '--ignore-scripts', '--no-audit', '--no-fund'], build, env, 300)
    commands.run('browser-web-build', [npm, 'run', 'build'], build, env, 300)
    helper = runpy.run_path(str(Path(__file__).with_name('artifacts.py')), run_name='fixed_wheel_builder')
    dist = build / 'dist'
    assert (dist / 'index.html').is_file()
    save('browser-dist-inventory.json', {'candidate': source['candidate_sha'],
         'entries': helper['bundle_inventory'](dist), 'ordinary_build': True})
    stage = root / 'browser-candidate-wheel'
    manifest = helper['build_origin']('wheel', 'candidate', candidate, source_record,
        stage, commands, api, uv, python, None, constraints, descriptor)
    driver = root / 'browser-driver'
    driver.mkdir(mode=0o700)
    for name in ('assistant_retirement_artifact_driver.py', 'assistant_retirement_native_observer.c'):
        shutil.copyfile(candidate / 'tests/helpers' / name, driver / name)
        assert file_receipt(driver / name) == {'path': str(driver / name),
            'sha256': file_receipt(candidate / 'tests/helpers' / name)['sha256']}
    binding = save('browser-stage-binding.json', {
        'manifest': file_receipt(manifest), 'driver': file_receipt(driver / 'assistant_retirement_artifact_driver.py'),
        'dist': str(dist), 'dist_inventory': file_receipt(api['receipts'] / 'browser-dist-inventory.json'),
        'node': file_receipt(node), 'chrome': file_receipt(chrome),
        'harness': file_receipt(Path(__file__).with_suffix('.mjs')), 'descriptor': descriptor,
        'run_root': str(stage / 'live-browser'), 'out': str(api['receipts'] / 'real-browser')})
    api['census'](commands, 'real-browser-before', python, driver / 'assistant_retirement_artifact_driver.py',
        descriptor, root, env, candidate, stage)
    _, code = commands.run('real-browser', [python, '-I', Path(__file__), binding], stage, env, 300, required=False)
    command_error = commands.rows[-1].get('error')
    api['census'](commands, 'real-browser-after', python, driver / 'assistant_retirement_artifact_driver.py',
        descriptor, root, env, candidate, stage)
    return {'exit': code, 'error': command_error,
        'receipt': file_receipt(api['receipts'] / 'real-browser/browser-driver.json'),
        'scope': 'candidate-wheel real daemon and ordinary dist, four locale/viewport cases; no overall PASS'}


def drive(binding_path):
    assert os.getuid() == os.geteuid() != 0 and sys.version_info[:3] == (3, 14, 4)
    binding = json.loads(binding_path.read_text())
    assert set(binding) == {'manifest', 'driver', 'dist', 'dist_inventory', 'node', 'chrome',
                            'harness', 'descriptor', 'run_root', 'out'}
    for key in ('manifest', 'driver', 'dist_inventory', 'node', 'chrome', 'harness'):
        assert file_receipt(binding[key]['path']) == binding[key]
    helper = runpy.run_path(binding['driver']['path'], run_name='fixed_browser_driver')
    helper['configure_native_observer'](binding['descriptor'])
    manifest = json.loads(Path(binding['manifest']['path']).read_text())
    helper['validate_origin'](manifest, 'wheel')
    assert manifest['source_role'] == 'candidate'
    artifacts = runpy.run_path(str(Path(__file__).with_name('artifacts.py')), run_name='fixed_bundle_observer')
    inventory = json.loads(Path(binding['dist_inventory']['path']).read_text())
    assert inventory['candidate'] == manifest['candidate_sha']
    assert inventory['entries'] == artifacts['bundle_inventory'](Path(binding['dist']))
    out = Path(binding['out']); out.mkdir(mode=0o700)
    driver = helper['Driver'](manifest, Path(binding['run_root']), 280)
    driver.env['HAPPYRANCH_WEB_DIST'] = binding['dist']
    result = {'status': 'failed', 'candidate': manifest['candidate_sha'], 'origin': 'wheel',
              'events': driver.events, 'launches': [], 'daemon_launches': [],
              'workload_uid': os.getuid(), 'mock_backend': False}
    children = []
    try:
        driver.start(); result['daemon_launches'].append(driver.identity)
        driver.lifecycle(); result['daemon_launches'].append(driver.identity)
        skeleton = driver.root / 'skeleton'
        agents = skeleton / 'org/agents'; agents.mkdir(parents=True)
        (skeleton / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent]\n')
        (skeleton / 'org/config.yaml').write_text('dreaming:\n  enabled: false\nworking_hours:\n  enabled: false\n')
        for name, role in (('engineering_head', 'manager'), ('dev_agent', 'worker')):
            (agents / (name + '.md')).write_text(f'---\nname: {name}\nteam: engineering\nrole: {role}\nexecutor: codex\nallow_rules: []\nrepos: {{}}\nmodel: null\n---\n\nYou are {name}.\n')
        driver.command(driver.cli + ['orgs', 'init', 'test', '--from', str(skeleton)])
        assert driver.request('GET', '/orgs')[1]['orgs'][0]['slug'] == 'test'
        assert driver.request('GET', '/metrics')[1]['executor_sessions_active'] == 0
        profile = driver.root / 'chrome-profile'; profile.mkdir(mode=0o700)
        with (driver.root / 'chrome.log').open('wb') as log:
            chrome = subprocess.Popen([binding['chrome']['path'], '--headless=new', '--disable-gpu',
                '--no-first-run', '--no-default-browser-check', '--disable-background-networking',
                '--disable-component-update', '--disable-sync', '--metrics-recording-only',
                '--password-store=basic', '--use-mock-keychain', '--remote-debugging-address=127.0.0.1',
                '--remote-debugging-port=0', '--user-data-dir=' + str(profile), 'about:blank'],
                cwd=driver.root, env=driver.env, stdout=log, stderr=log, start_new_session=True)
            children.append(chrome)
            chrome_identity = next(row for row in helper['process_table']() if row['pid'] == chrome.pid)
            assert chrome_identity['exe'] == binding['chrome']['path']
            assert chrome_identity['uid'] == os.getuid()
            driver.launches.append(chrome_identity); result['launches'].append(chrome_identity)
            port_file = profile / 'DevToolsActivePort'
            limit = time.monotonic() + 15
            while not port_file.exists():
                assert chrome.poll() is None and time.monotonic() < limit, 'owned Chrome readiness unavailable'
                time.sleep(.1)
            port, endpoint = port_file.read_text().splitlines()[:2]
            assert port.isdecimal() and 0 < int(port) < 65536 and endpoint.startswith('/devtools/browser/')
            node_binding = driver.root / 'browser-binding.json'
            node_binding.write_text(json.dumps({'base': driver.base.removesuffix('/api/v1') + '/',
                'devtools': 'ws://127.0.0.1:' + port + endpoint, 'out': str(out)}))
            with (driver.root / 'node.log').open('wb') as node_log:
                node = subprocess.Popen([binding['node']['path'], binding['harness']['path'], str(node_binding)],
                    cwd=driver.root, env=driver.env, stdout=node_log, stderr=node_log, start_new_session=True)
                children.append(node)
                identity = next(row for row in helper['process_table']() if row['pid'] == node.pid)
                assert identity['exe'] == binding['node']['path']
                assert identity['uid'] == os.getuid()
                driver.launches.append(identity); result['launches'].append(identity)
                result['node_exit'] = node.wait(timeout=210)
            result['chrome_exit'] = chrome.wait(timeout=15)
        assert result['node_exit'] == result['chrome_exit'] == 0
        cases = json.loads((out / 'browser-cases.json').read_text())
        assert cases['status'] == 'passed' and len(cases['cases']) == 4
        assert {(r['locale'], r['width'], r['height']) for r in cases['cases']} == {
            (locale, width, height) for locale in ('en', 'zh-CN') for width, height in ((390, 844), (1440, 900))}
        assert all(r['status'] == 'passed' for r in cases['cases'])
        assert len(list(out.glob('*.png'))) == 8
        metrics = driver.request('GET', '/metrics')[1]
        assert metrics['executor_sessions_active'] == 0
        result['zero_executor_sessions'] = True
        assert not (driver.runtime / 'system').exists()
        assert inventory['entries'] == artifacts['bundle_inventory'](Path(binding['dist']))
        result['status'] = 'passed'
    except Exception as error:
        result['error'] = {'type': type(error).__name__, 'message': str(error)}
    finally:
        # Existing ordinary cleanup uses full native identities and exact owned starts.
        # Only the fixed observer is root; no browser/daemon signal is elevated.
        try:
            driver.stop()
        except Exception as error:
            result['status'] = 'failed'
            result['cleanup_error'] = {'type': type(error).__name__, 'message': str(error)}
        for child in children:
            try:
                # An unreaped direct Popen child cannot be a reused PID. Preserve
                # unavailable-census failure, but still stop/reap this owned child.
                if child.poll() is None:
                    child.terminate()
                    result.setdefault('direct_child_termination', []).append(child.pid)
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                result['status'] = 'failed'
                child.kill()
                try:
                    child.wait(timeout=5)
                    result.setdefault('direct_child_kill', []).append(child.pid)
                except subprocess.TimeoutExpired:
                    result['child_reap_unavailable'] = child.pid
        daemon_log = driver.root / 'daemon.log'
        if daemon_log.exists():
            raw = daemon_log.read_bytes()
            assert len(raw) <= 1024 * 1024
            text = raw.decode('utf-8', errors='strict')
            result['daemon_log_original'] = {'sha256': hashlib.sha256(raw).hexdigest(),
                'bytes': len(raw), 'fixture_token_redactions': text.count(driver.token) if driver.token else 0}
            (out / 'daemon.log').write_text(text.replace(driver.token, '[fixture-token-redacted]') if driver.token else text)
        for name in ('node.log', 'chrome.log'):
            path = driver.root / name
            if path.exists():
                assert path.stat().st_size <= 1024 * 1024
                shutil.copyfile(path, out / name)
        result['files'] = {p.name: file_receipt(p) for p in out.iterdir() if p.is_file()}
        (out / 'browser-driver.json').write_text(json.dumps(result, indent=2) + '\n')
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    assert len(sys.argv) == 2
    raise SystemExit(drive(Path(sys.argv[1])))
