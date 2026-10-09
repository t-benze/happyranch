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
import stat
import subprocess
import sys
import time
import tarfile
import urllib.request


def file_receipt(path):
    path = Path(path).resolve(strict=True)
    assert path.is_file() and not path.stat().st_mode & 0o022
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


# Immutable official Google Packages metadata observed before this evidence edit.
CHROME_URL = 'https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_155.0.8059.39-1_amd64.deb'
CHROME_SHA256 = 'c58aa0f2cd66179c9f050e062c882d27aa9b9f8c2b7c73fee3498560b5ed0b38'
CHROME_SIZE = 143552428
CHROME_METADATA_SHA256 = '15c94819fc901aac960b974ddb747a45ec5f1cc85b0f30e3adbb02642e23bdbb'


def prepare_linux_chrome(commands, root, env, save):
    """Read a fixed authenticated archive into an ordinary protected prefix.

    Never install a package, execute maintainer scripts, alter the host or
    materialize the privileged sandbox helper. Default sandbox remains required.
    """
    assert sys.platform == 'linux' and os.getuid() == os.geteuid() != 0
    tool = Path('/usr/bin/dpkg-deb')
    for path in (tool, *tool.parents):
        info = path.lstat()
        assert not stat.S_ISLNK(info.st_mode) and info.st_uid == 0
        assert not info.st_mode & 0o022, f'unprotected archive tool origin: {path}'
    tool_origin = file_receipt(tool)
    version, _ = commands.run('browser-chrome-archive-tool-version', [tool, '--version'], root, env)
    archive = root / 'browser-chrome.deb'
    digest = hashlib.sha256(); size = 0
    with urllib.request.urlopen(CHROME_URL, timeout=30) as response, archive.open('xb') as sink:
        assert response.geturl() == CHROME_URL and response.status == 200
        for block in iter(lambda: response.read(1024 * 1024), b''):
            size += len(block)
            assert size <= CHROME_SIZE
            digest.update(block); sink.write(block)
    archive.chmod(0o400)
    assert size == CHROME_SIZE and digest.hexdigest() == CHROME_SHA256
    archive_receipt = file_receipt(archive)
    save('browser-linux-chrome-download.json', {'url': CHROME_URL, 'size': size,
        'sha256': digest.hexdigest(), 'official_metadata_url':
        'https://dl.google.com/linux/chrome/deb/dists/stable/main/binary-amd64/Packages',
        'official_metadata_sha256': CHROME_METADATA_SHA256,
        'authentication': 'immutable official HTTPS archive SHA256 and size pin',
        'archive': archive_receipt, 'workload_uid': os.getuid()})
    # dpkg-deb streams data only; this command cannot invoke package scripts.
    tar = root / 'browser-chrome-data.tar'
    started = time.monotonic()
    with tar.open('xb') as sink:
        unpack = subprocess.run([str(tool), '--fsys-tarfile', str(archive)], cwd=root,
            env=env, stdout=sink, stderr=subprocess.PIPE, timeout=120, check=False)
    tar.chmod(0o400)
    assert len(unpack.stderr) <= 16384
    save('browser-linux-chrome-unpack.json', {'argv': [str(tool), '--fsys-tarfile', str(archive)],
        'tool': tool_origin, 'tool_version': version, 'exit': unpack.returncode,
        'stderr': unpack.stderr.decode('utf-8', errors='strict'),
        'duration_seconds': time.monotonic() - started, 'ordinary_uid': os.getuid(),
        'data_archive_size': tar.stat().st_size})
    assert unpack.returncode == 0 and tar.stat().st_size <= 600 * 1024 * 1024
    assert file_receipt(tool) == tool_origin and file_receipt(archive) == archive_receipt
    destination = root / 'browser-chrome'; destination.mkdir(mode=0o700)
    entries = {}; names = set(); total = 0
    with tarfile.open(tar, 'r|') as stream:
        for member in stream:
            name = member.name.removeprefix('./')
            assert not Path(name).is_absolute() and '..' not in Path(name).parts
            assert name not in names and len(names) < 2000
            names.add(name)
            assert 0 <= member.size <= 300 * 1024 * 1024
            total += member.size
            assert total <= 512 * 1024 * 1024
            if not name.startswith('opt/google/chrome/'):
                continue
            relative = name.removeprefix('opt/google/chrome/')
            if not relative:
                assert member.isdir()
                continue
            assert member.uid == 0 and member.gid == 0
            target = destination / relative
            if member.isdir():
                target.mkdir(mode=0o700, parents=True, exist_ok=True)
                continue
            assert member.isfile(), f'nonregular Chrome package member: {relative}'
            assert relative not in entries and len(entries) < 2000
            # Hash excluded setuid helper directly from archive, never extract it.
            copied = relative != 'chrome-sandbox'
            if copied:
                assert not member.mode & (stat.S_ISUID | stat.S_ISGID)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            sha = hashlib.sha256(); count = 0
            sink = target.open('xb') if copied else None
            try:
                with stream.extractfile(member) as source:
                    for block in iter(lambda: source.read(1024 * 1024), b''):
                        count += len(block); assert count <= member.size
                        sha.update(block)
                        if sink is not None:
                            sink.write(block)
            finally:
                if sink is not None:
                    sink.close()
            assert count == member.size
            row = {'sha256': sha.hexdigest(), 'size': count, 'archive_uid': member.uid,
                'archive_mode': member.mode, 'copied': copied}
            if copied:
                target.chmod(0o500 if member.mode & 0o111 else 0o400)
                row['copy'] = file_receipt(target); row['copy_mode'] = target.stat().st_mode
            entries[relative] = row
    assert 'chrome' in entries and 'chrome-sandbox' in entries
    assert file_receipt(archive) == archive_receipt and file_receipt(tool) == tool_origin
    origin = {'schema_version': 2, 'source': CHROME_URL, 'destination': str(destination),
        'package': 'google-chrome-stable', 'version': '155.0.8059.39-1',
        'archive': archive_receipt, 'archive_size': CHROME_SIZE,
        'official_metadata_sha256': CHROME_METADATA_SHA256, 'tool': tool_origin,
        'entries': entries, 'bytes': total, 'workload_uid': os.getuid(),
        'sandbox_flags': [], 'excluded_privileged_member': 'chrome-sandbox'}
    record = save('browser-linux-chrome-origin.json', origin)
    validate_linux_chrome(record, destination / 'chrome')
    return destination / 'chrome', file_receipt(record)


def validate_linux_chrome(record, executable):
    """Revalidate the pinned archive and every closed protected extracted member."""
    value = json.loads(Path(record).read_text())
    assert set(value) == {'schema_version', 'source', 'destination', 'package', 'version',
        'archive', 'archive_size', 'official_metadata_sha256', 'tool', 'entries', 'bytes',
        'workload_uid', 'sandbox_flags', 'excluded_privileged_member'}
    assert value['schema_version'] == 2 and value['source'] == CHROME_URL
    assert value['package'] == 'google-chrome-stable' and value['version'] == '155.0.8059.39-1'
    assert value['archive_size'] == CHROME_SIZE and value['archive']['sha256'] == CHROME_SHA256
    assert value['official_metadata_sha256'] == CHROME_METADATA_SHA256
    archive = Path(value['archive']['path'])
    assert archive.name == 'browser-chrome.deb' and archive.stat().st_size == CHROME_SIZE
    assert file_receipt(archive) == value['archive']
    assert value['tool']['path'] == '/usr/bin/dpkg-deb'
    assert file_receipt(value['tool']['path']) == value['tool']
    assert value['workload_uid'] == os.getuid() != 0 and value['sandbox_flags'] == []
    assert value['excluded_privileged_member'] == 'chrome-sandbox'
    assert 0 < len(value['entries']) <= 2000 and 0 < value['bytes'] <= 512 * 1024 * 1024
    destination = Path(value['destination'])
    assert destination.is_absolute() and destination.name == 'browser-chrome'
    assert archive.parent == destination.parent and Path(executable) == destination / 'chrome'
    actual = set()
    for directory, directories, files in os.walk(destination, followlinks=False):
        for name in (None, *directories):
            path = Path(directory) if name is None else Path(directory) / name
            info = path.lstat()
            assert stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid() and not info.st_mode & 0o022
        for name in files:
            path = Path(directory) / name; relative = path.relative_to(destination).as_posix()
            actual.add(relative); assert len(actual) <= 2000
            row = value['entries'][relative]; info = path.lstat()
            assert set(row) == {'sha256', 'size', 'archive_uid', 'archive_mode', 'copied', 'copy', 'copy_mode'}
            assert row['archive_uid'] == 0 and 0 <= row['size'] <= 300 * 1024 * 1024
            assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            assert not info.st_mode & (stat.S_ISUID | stat.S_ISGID | 0o022)
            assert row['copied'] and row['copy']['path'] == str(path)
            assert info.st_mode == row['copy_mode'] and info.st_size == row['size']
            assert file_receipt(path) == row['copy'] and row['sha256'] == row['copy']['sha256']
    assert actual == {name for name, row in value['entries'].items() if row['copied']}
    assert 'chrome-sandbox' not in actual and 'chrome' in actual


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
    chrome_origin = None
    if sys.platform == 'linux':
        chrome, chrome_origin = prepare_linux_chrome(commands, root, env, save)
    else:
        chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    assert chrome.is_file(), 'fixed hosted native Chrome executable unavailable'
    chrome_version, _ = commands.run('browser-chrome-version', [chrome, '--version'], root, env)
    dependencies, _ = commands.run('browser-chrome-native-dependencies',
        ['/usr/bin/otool', '-L', chrome] if sys.platform == 'darwin' else ['/usr/bin/ldd', chrome], root, env)
    save('browser-tools.json', {'node': file_receipt(node), 'node_version': version.strip(),
         'npm': file_receipt(npm), 'npm_version': npm_version.strip(),
         'chrome': file_receipt(chrome), 'chrome_version': chrome_version.strip(),
         'chrome_origin': chrome_origin,
         'native_dependencies_sha256': hashlib.sha256(dependencies.encode()).hexdigest(),
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
        'node': file_receipt(node), 'chrome': file_receipt(chrome), 'chrome_origin': chrome_origin,
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
    assert set(binding) == {'manifest', 'driver', 'dist', 'dist_inventory', 'node', 'chrome', 'chrome_origin',
                            'harness', 'descriptor', 'run_root', 'out'}
    for key in ('manifest', 'driver', 'dist_inventory', 'node', 'chrome', 'harness'):
        assert file_receipt(binding[key]['path']) == binding[key]
    if sys.platform == 'linux':
        assert file_receipt(binding['chrome_origin']['path']) == binding['chrome_origin']
        validate_linux_chrome(binding['chrome_origin']['path'], binding['chrome']['path'])
    else:
        assert binding['chrome_origin'] is None
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
        (skeleton / 'org/teams.yaml').write_text('teams:\n  engineering:\n    manager: engineering_head\n    workers: [dev_agent, code_reviewer]\n')
        (skeleton / 'org/config.yaml').write_text('dreaming:\n  enabled: false\nworking_hours:\n  enabled: false\n')
        for name, role in (('engineering_head', 'manager'), ('dev_agent', 'worker'), ('code_reviewer', 'worker')):
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
                'devtools': 'ws://127.0.0.1:' + port + endpoint, 'out': str(out), 'platform': sys.platform}))
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
        if sys.platform == 'linux':
            try:
                validate_linux_chrome(binding['chrome_origin']['path'], binding['chrome']['path'])
                result['chrome_origin_unchanged'] = True
            except Exception as error:
                result['status'] = 'failed'
                result['chrome_origin_error'] = {'type': type(error).__name__, 'message': str(error)}
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
