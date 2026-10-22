"""Finite manual packaging cases 1–5. No pytest or runtime imports; not a CI gate.

Uses authentic retained originals, synthetic resolved manifests/candidate, and
an explicitly substituted ditto ZIP writer. Runs the shipping collector and
archive transition plus unchanged resource/inventory/release readers on Linux.
"""
from __future__ import annotations

import argparse
import ast
import copy
import hashlib
import importlib.metadata
import json
import os
import plistlib
import runpy
import shutil
import subprocess
import struct
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

SOURCE = Path(__file__).resolve().parent.parent
H = runpy.run_path(str(SOURCE / 'app/mac/scripts/manifest_notices.py'))
REPORT = []


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else H['encode'](value))


def call(label, argv, expected=0, env=None):
    p = subprocess.run(argv, capture_output=True, text=True, env=env)
    REPORT.append(dict(label=label, argv=[str(x) for x in argv], exit=p.returncode,
                       stdout=p.stdout[-4000:], stderr=p.stderr[-4000:]))
    assert (p.returncode == 0) == (expected == 0), (label, p.returncode, p.stdout, p.stderr)
    return p


def python_fixture_snapshot(source, evidence, extra=()):
    """Synthetic freezer/toolchain facts; real metadata, snapshot and originals."""
    prefix = source / 'fixture-python'
    library = prefix / 'lib/libpython3.12.dylib'
    stdlib = prefix / 'lib/python3.12/lib-dynload/_struct.cpython-312-darwin.so'
    # Keep a RECORD-owned native input separate from interpreter ownership.
    pyroot = source / '.venv/lib/python3.12/site-packages'
    dist_library = pyroot / 'PyInstaller/vendor/libfixture.dylib'
    record = pyroot / 'pyinstaller.dist-info/RECORD'
    data = record.read_bytes()
    entry = b'PyInstaller/vendor/libfixture.dylib,,\n'
    if entry not in data:
        write(record, data + entry)
    binaries = []
    for path in (library, stdlib, dist_library, *extra):
        write(path, b'synthetic Analysis native input: ' + path.name.encode())
        binaries.append(dict(source=str(path), destination=path.name, kind='BINARY',
                             sha256=H['sha'](path.read_bytes())))
    analysis = {lane: dict(binaries=copy.deepcopy(binaries)) for lane in ('daemon', 'cli')}
    selected = {lane: str(library) for lane in ('daemon', 'cli')}
    component = dict(version='3.12.10', notices=[dict(sha256=H['sha']((evidence / 'notices/Python-LICENSE.txt').read_bytes()))])
    H['python_snapshot'](list(importlib.metadata.distributions(path=[str(pyroot)])),
                         analysis, component, evidence, prefix, selected)
    snapshot = json.loads((evidence / 'python-distributions.json').read_text())
    REPORT.append(dict(label='R1 real python_snapshot', exit=0, selected=selected,
                       analysis=analysis, runtime=snapshot['runtime'],
                       substitutions='Synthetic EXE.python_lib values for selected standalone CPython 3.12.10, prefix, Analysis bytes/rows and installed RECORD; no sysconfig or native toolchain is executed or claimed.'))
    return snapshot


def fixture(root):
    source, output, app = root / 'source', root / 'output', root / 'output/HappyRanch.app'
    (output / 'diagnostics').mkdir(parents=True)
    app.mkdir()
    write(source / 'LICENSE', (SOURCE / 'LICENSE').read_bytes())
    originals = SOURCE / 'web/vendor/react-remove-scroll-bar'
    locks = {'': {'name': 'fixture', 'version': '1.0.1'}}
    for location, version in [('node_modules/react-remove-scroll-bar', '2.3.7+upstream.8ca9ba5ea52d'),
                              ('node_modules/nested/node_modules/react-remove-scroll-bar', '2.3.7')]:
        locks[location] = dict(version=version, resolved='pinned upstream 8ca9ba5ea52de03308fe8ced94f7b159a44d28ff')
        write(source / 'web' / location / 'package.json', dict(name='react-remove-scroll-bar', version=version))
        write(source / 'web' / location / 'LICENSE', (originals / 'LICENSE').read_bytes())
    # Generated-web tooling is included directly from resolved manifests.
    locks['node_modules/vite'] = dict(version='5.4.21+hr.css-relation.1', dev=True)
    write(source / 'web/node_modules/vite/package.json', dict(name='vite', version='5.4.21+hr.css-relation.1'))
    write(source / 'web/node_modules/vite/LICENSE', (SOURCE / 'web/vendor/vite/LICENSE.md').read_bytes())
    locks['node_modules/typescript'] = dict(version='5.0.0', dev=True)
    write(source / 'web/node_modules/typescript/package.json', dict(name='typescript', version='5.0.0'))
    locks['node_modules/@esbuild/linux-x64'] = dict(version='0.21.5', optional=True, dev=True, os=['linux'], cpu=['x64'])
    write(source / 'web/package-lock.json', dict(lockfileVersion=3, packages=locks))
    pyversion = next(p['version'] for p in __import__('tomllib').loads((SOURCE / 'uv.lock').read_text())['package'] if p['name'] == 'pyinstaller' and 'path' in p['source'])
    pyroot = source / '.venv/lib/python3.12/site-packages'
    pylicense = pyroot / 'pyinstaller.dist-info/COPYING.txt'
    write(pylicense, (SOURCE / 'packaging/vendor/pyinstaller-6.21.0/original/COPYING.txt').read_bytes())
    write(pylicense.parent / 'METADATA', ('Metadata-Version: 2.1\nName: PyInstaller\nVersion: ' + pyversion + '\n').encode())
    write(pylicense.parent / 'RECORD', b'pyinstaller.dist-info/COPYING.txt,,\npyinstaller.dist-info/METADATA,,\npyinstaller.dist-info/RECORD,,\n')
    write(source / 'uv.lock', ('version = 1\n[[package]]\nname = "pyinstaller"\nversion = "' + pyversion + '"\nsource = { path = "packaging/vendor/pyinstaller-6.21.0" }\n').encode())
    pywork = output / 'scratch/freeze-work'
    write(pywork / 'notices/Python-LICENSE.txt', (SOURCE / 'packaging/notice-data/cpython-LICENSE').read_bytes())
    python_fixture_snapshot(source, pywork)
    gomod = output / 'scratch/go-modules/github.com/google/uuid@v1.6.0'
    write(gomod / 'LICENSE', (SOURCE / 'packaging/fixtures/manifest-notices/google-uuid-LICENSE').read_bytes())
    module = dict(Path='github.com/google/uuid', Version='v1.6.0', Dir=str(gomod))
    write(output / 'diagnostics/go-modules.json', module)
    write(output / 'diagnostics/go-deps.json', dict(ImportPath='github.com/google/uuid', Module=module))
    for filename in ('go.mod', 'go.sum'):
        write(source / 'app/mac/tsnet-bridge' / filename, (SOURCE / 'app/mac/tsnet-bridge' / filename).read_bytes())
    shutil.copytree(SOURCE / 'web/public', source / 'web/public')
    shutil.copytree(SOURCE / 'web/public', app / 'Contents/Resources/web/dist')
    shutil.copytree(SOURCE / 'app/mac/Resources/AppIcon.appiconset', source / 'app/mac/Resources/AppIcon.appiconset')
    write(source / 'packaging/THIRD_PARTY_NOTICES.md', (SOURCE / 'packaging/THIRD_PARTY_NOTICES.md').read_bytes())
    write(source / 'packaging/notice-data/font-assets.json', (SOURCE / 'packaging/notice-data/font-assets.json').read_bytes())
    # Authentic retained Go release originals; fixture needs no Go installation.
    goroot = source / 'fixture-go'
    go_facts = json.loads((SOURCE / 'packaging/fixtures/manifest-notices/go-provenance.json').read_text())
    goversion = go_facts[0]['version']
    for fact in go_facts:
        raw = (SOURCE / 'packaging/fixtures/manifest-notices' / fact['file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == fact['sha256']
        write(goroot / fact['file'].removeprefix('go-'), raw)
    mapping = dict(source_tree='2' * 40, go_root=str(goroot), go_version=goversion,
                   go_originals=[str(goroot / 'LICENSE'), str(goroot / 'PATENTS')], components=[])
    write(output / 'diagnostics/license-mapping.json', mapping)
    # Copy accepted actual resources and assert baseline identities independently.
    script = (SOURCE / 'app/mac/scripts/verify-release.sh').read_text().split("<<'PYTHON'\n", 1)[1].rsplit('\nPYTHON', 1)[0]
    tree = ast.parse(script)
    baseline = next(ast.literal_eval(node.value) for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'BASELINE' for t in node.targets))
    for row in baseline:
        original = SOURCE / row['path']
        data = original.read_bytes()
        assert hashlib.sha256(data).hexdigest() == row['sha256'], row['path']
        dest = app / ('Contents/Resources/licenses/LICENSE' if row['path'] == 'LICENSE' else 'Contents/Resources/daemon/_internal/' + row['path'])
        write(dest, data)
        dest.chmod(int(row['posix_mode'], 8))
    plist = plistlib.loads((SOURCE / 'app/mac/scripts/Info.plist').read_bytes())
    plist.update(dict(CFBundleShortVersionString='1.0.1', CFBundleVersion='1.0.1', GitCommitSHA='1' * 40, GitTreeSHA='2' * 40, ReleaseArchitecture='arm64'))
    write(app / 'Contents/Info.plist', plistlib.dumps(plist))
    for name in ('Contents/MacOS/HappyRanch', 'Contents/Resources/daemon/happyranch-daemon', 'Contents/Resources/daemon/happyranch'):
        write(app / name, struct.pack('<IIIIIIII', 0xfeedfacf, 0x100000c, 0, 2, 0, 0, 0, 0))
        (app / name).chmod(0o755)
    write(app / 'Contents/Resources/AppIcon.icns', b'preassembled icon fixture, not iconutil output')
    write(app / 'Contents/Resources/web/dist/index.html', b'<html>preassembled fixture</html>')
    return source, output, app, mapping


def python_spec_notices(scratch, stdlib=None):
    """Run the shipping notice block, without PyInstaller or a native build."""
    spec_path = SOURCE / 'packaging/daemon.spec'
    nodes = ast.parse(spec_path.read_text()).body
    start = next(i for i, node in enumerate(nodes) if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name) and t.id == 'notice_root' for t in node.targets))
    stop = next(i for i, node in enumerate(nodes) if isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Subscript)
                and isinstance(node.value.func.slice, ast.Constant)
                and node.value.func.slice.value == 'python_snapshot')
    block = compile(ast.Module(body=nodes[start:stop], type_ignores=[]), str(spec_path), 'exec')
    helpers = runpy.run_path(str(SOURCE / 'packaging/notice-data/retained_notices.py'))
    identity = helpers['identities'](SOURCE / 'packaging/notice-data')['python']
    original = (SOURCE / 'packaging/notice-data/cpython-LICENSE').read_bytes()
    cases = [('installed CPython plus vendor', True, True, None),
             ('missing CPython with vendor refuses unsupported identity', False, True, 'PYTHON_NOTICE_IDENTITY_MISMATCH'),
             ('missing CPython without vendor refuses unsupported identity', False, False, 'PYTHON_NOTICE_IDENTITY_MISMATCH')]
    if stdlib is not None:
        cases += [('uv installed stdlib original plus vendor', False, True, None),
                  ('uv installed stdlib original tampered', False, True, 'PYTHON_NOTICE_ORIGINAL_MISMATCH'),
                  ('authenticated fallback plus vendor', False, True, None),
                  ('authenticated fallback stdlib mismatch', False, True, 'PYTHON_NOTICE_STDLIB_MISMATCH'),
                  ('authenticated fallback original missing', False, True, 'cpython-LICENSE')]
    else:
        REPORT.append(dict(label='authenticated fallback positive', result='NOT RUN',
                           limitation='Supply --cpython-stdlib with the three pinned source originals; native runtime acceptance remains UNEXECUTED.'))
    for index, (label, installed, vendor, error) in enumerate(cases):
        root = scratch / ('spec-' + str(index))
        prefix, evidence = root / 'python', root / 'evidence'
        prefix.mkdir(parents=True); evidence.mkdir()
        if installed:
            write(prefix / 'LICENSE.txt', original)
        if vendor:
            write(prefix / 'licenses/vendor.txt', b'Supplemental vendor original\n')
        effective = SimpleNamespace(base_prefix=str(prefix), implementation=SimpleNamespace(name='cpython'),
                                    executable=str(prefix / 'bin/python'), version=sys.version, platform=sys.platform)
        machine = 'unsupported-fixture'
        analysis = {}
        selected_stdlib = prefix / 'lib/python3.12'
        selected_stdlib.mkdir(parents=True)
        if label.startswith('uv installed'):
            # Actual native23 diagnostic metadata; interpreter fields are still
            # synthetic here. Bytes come from the authenticated uv archive.
            effective.version = '3.12.10 (main, May 30 2025, 05:53:56) [Clang 20.1.4 ]'
            effective.platform, machine = 'darwin', 'arm64'
            write(selected_stdlib / 'LICENSE.txt', (stdlib / 'LICENSE.txt').read_bytes())
            if error == 'PYTHON_NOTICE_ORIGINAL_MISMATCH':
                write(selected_stdlib / 'LICENSE.txt', b'tampered CPython original')
        if label.startswith('authenticated'):
            effective.version = identity['toolchain']['version']
            effective.platform = identity['toolchain']['platform']
            machine = identity['toolchain']['machine']
            selected_stdlib = prefix / 'lib/python3.12'
            for filename in identity['stdlib']:
                write(selected_stdlib / filename, (stdlib / filename).read_bytes())
            analysis = {lane: dict(pure=[dict(destination=Path(name).stem, source=str(selected_stdlib / name),
                        kind='PYMODULE', sha256=digest) for name, digest in identity['stdlib'].items()])
                        for lane in ('daemon', 'cli')}
            if error == 'PYTHON_NOTICE_STDLIB_MISMATCH':
                write(selected_stdlib / 'threading.py', b'changed stdlib fixture')
        write(evidence / 'python-toolchain.json', dict(executable=effective.executable,
              version=effective.version, platform=effective.platform, machine=machine))
        spec_dir = SOURCE / 'packaging'
        if label == 'authenticated fallback original missing':
            spec_dir = root / 'packaging'
            for name in ('retained_notices.py', 'identity-map.json'):
                write(spec_dir / 'notice-data' / name, (SOURCE / 'packaging/notice-data' / name).read_bytes())
        env = dict(Path=Path, sys=effective, os=os, json=json, runpy=runpy,
                   _SPEC_DIR=spec_dir, _PROJ=SOURCE, evidence=evidence, analysis=analysis)
        try:
            with patch('platform.machine', return_value=machine), patch('sysconfig.get_path', return_value=str(selected_stdlib)):
                exec(block, env)
        except (ValueError, FileNotFoundError) as exc:
            assert error and error in str(exc), (label, str(exc))
            assert not list((evidence / 'notices').iterdir()), label
            REPORT.append(dict(label=label, result='REFUSED', error=str(exc)))
        else:
            assert error is None, (label, 'missing CPython original accepted')
            retained = [Path(n['retained']).read_bytes() for n in env['python_component']['notices']]
            assert retained == [original] + ([b'Supplemental vendor original\n'] if vendor else []), label
            REPORT.append(dict(label=label, result='PASS', retained_sha256=[H['sha'](b) for b in retained],
                               limitation='Synthetic effective identity/Analysis; authentic pinned notice/stdlib bytes. No native interpreter executed.'))


def run(scratch, stdlib=None):
    os.umask(0o077)  # Match shipping private output allocations.
    scratch.mkdir(mode=0o700)
    python_spec_notices(scratch, stdlib)
    source, output, app, mapping = fixture(scratch)
    facts = REPORT[-1]
    for label in ('missing selected Analysis input', 'different lane selection'):
        analysis, selected = copy.deepcopy(facts['analysis']), dict(facts['selected'])
        if label.startswith('missing'):
            analysis['cli']['binaries'] = [r for r in analysis['cli']['binaries'] if r['source'] != selected['cli']]
        else:
            selected['cli'] = analysis['cli']['binaries'][1]['source']
        refused_output = scratch / ('r1-refused-' + str(len(REPORT)))
        try:
            H['python_snapshot'](list(importlib.metadata.distributions(path=[str(source / '.venv/lib/python3.12/site-packages')])),
                                 analysis, dict(version='3.12.10', notices=[]), refused_output,
                                 source / 'fixture-python', selected)
        except ValueError as exc:
            assert 'MANIFEST_NOTICE_REFUSED' in str(exc) and 'interpreter librar' in str(exc)
            REPORT.append(dict(label='R1 ' + label, result='REFUSED', error=str(exc),
                               selected=selected, analysis=analysis))
        else:
            raise AssertionError('inconsistent freezer selection accepted')
        assert not (refused_output / 'python-distributions.json').exists()
    cli = [sys.executable, str(SOURCE / 'app/mac/scripts/manifest_notices.py')]
    collect = cli + ['collect', '--source', str(source), '--app', str(app), '--output', str(output), '--mapping', str(output / 'diagnostics/license-mapping.json')]
    py_snapshot = json.loads((output / 'scratch/freeze-work/python-distributions.json').read_text())
    assert [r['key'] for r in py_snapshot['runtime']] == ['Python']
    assert py_snapshot['runtime'][0]['interpreter_library']['source'].endswith('/lib/libpython3.12.dylib')
    assert py_snapshot['distributions'][0]['name'] == 'PyInstaller'
    call('case1 real collector complete resolved fixture including R1 interpreter library', collect)
    call('case1 independent live inventory verification', ['bash', str(SOURCE / 'app/mac/scripts/verify-release.sh'), 'licenses', '--source', str(source), '--app', str(app), '--output-dir', str(output), '--mapping', str(output / 'diagnostics/license-mapping.json')])
    manifest_path = app / H['MANIFEST']
    manifest_raw = manifest_path.read_bytes()
    manifest = json.loads(manifest_raw)
    # Only the native compression executable is substituted. The shipping shell
    # and resource/archive/reconciliation readers below are unmodified.
    bindir = scratch / 'bin'
    bindir.mkdir()
    ditto = bindir / 'ditto'
    ditto.write_text('#!' + sys.executable + '\nimport pathlib,sys,zipfile\napp=pathlib.Path(sys.argv[-2]); dest=pathlib.Path(sys.argv[-1])\nassert sys.argv[1:4]==["-c","-k","--keepParent"]\nwith zipfile.ZipFile(dest,"x",zipfile.ZIP_DEFLATED) as z:\n for p in sorted(app.rglob("*")): z.write(p,"HappyRanch.app/"+p.relative_to(app).as_posix())\n')
    ditto.chmod(0o755)
    env = dict(os.environ, PATH=str(bindir) + ':' + os.environ['PATH'])
    archive = ['bash', str(SOURCE / 'app/mac/scripts/archive-app.sh'), str(app), str(output), '1.0.1', '1.0.1', '1' * 40, '2' * 40, 'arm64', 'portable-fixture']
    candidate_zip = output / 'HappyRanch-1.0.1-1.0.1-arm64.zip'
    call('case1 actual shipping archive transition (ditto substituted)', archive, env=env)
    assert candidate_zip.is_file()
    python_owner = next(r for r in manifest['components'] if r['id'] == 'runtime:Python')
    assert python_owner['notices']
    with zipfile.ZipFile(candidate_zip) as z:
        for notice in python_owner['notices']:
            assert z.read('HappyRanch.app/' + notice['destination']) == (SOURCE / 'packaging/notice-data/cpython-LICENSE').read_bytes()
    REPORT.append(dict(label='R1 actual ZIP contains authentic interpreter notice', exit=0,
                       archive=str(candidate_zip), sha256=H['sha'](candidate_zip.read_bytes()),
                       size=candidate_zip.stat().st_size, notices=python_owner['notices']))
    valid_zip = scratch / 'complete.zip'
    shutil.copyfile(candidate_zip, valid_zip)
    candidate_zip.unlink()  # exact fixture-owned generated archive, for refusal cases
    # Misleading basename and prefix membership must not inherit CPython terms.
    for index, name in enumerate(('lib/vendor/libpython3.12.dylib', 'vendor/Python')):
        bad_source, bad_output, bad_app, _ = fixture(scratch / ('r1-vendor-' + str(index)))
        work = bad_output / 'scratch/freeze-work'
        (work / 'python-distributions.json').unlink()
        vendor = bad_source / 'fixture-python' / name
        bad_snapshot = python_fixture_snapshot(bad_source, work, (vendor,))
        unknown = next(r for r in bad_snapshot['runtime'] if r['key'] == str(vendor))
        assert unknown['originals'] == []
        args = cli + ['collect', '--source', str(bad_source), '--app', str(bad_app), '--output', str(bad_output), '--mapping', str(bad_output / 'diagnostics/license-mapping.json')]
        refused = call('R1 misleading unknown vendor collector refusal ' + name, args, expected=1)
        assert str(vendor) in refused.stderr and 'no original' in refused.stderr
        assert not (bad_app / H['MANIFEST']).exists()
        bad_archive = archive.copy()
        bad_archive[2:4] = [str(bad_app), str(bad_output)]
        call('R1 uncollected vendor candidate refuses before archive ' + name, bad_archive, expected=1, env=env)
        assert not (bad_output / candidate_zip.name).exists()
    for family in ('npm', 'go', 'python', 'asset', 'runtime', 'font'):
        broken = copy.deepcopy(manifest)
        removed = next(r for r in broken['components'] if (r['family'] == family or (family == 'font' and r['family'] == 'asset' and 'Newsreader' in r['component'])) and r['selection'].startswith('included:'))
        broken['components'].remove(removed)
        write(manifest_path, broken)
        p = call('case2 missing owner ' + removed['id'], archive, expected=1, env=env)
        assert removed['id'] in p.stderr and not candidate_zip.exists()
    write(manifest_path, manifest_raw)
    broken = copy.deepcopy(manifest)
    runtime = next(r for r in broken['components'] if r['id'] == 'runtime:Go')
    assert len(runtime['notices']) == 2
    runtime['notices'].pop()
    write(manifest_path, broken)
    call('case2 omitted second applicable Go PATENTS original', archive, expected=1, env=env)
    assert not candidate_zip.exists()
    write(manifest_path, manifest_raw)
    for family in ('python', 'asset', 'runtime:Python'):
        row = next(r for r in manifest['components'] if (r['family'] == family or r['id'] == family) and r['notices'])
        notice = app / row['notices'][0]['destination']
        data = notice.read_bytes()
        notice.unlink()
        call('case2 missing retained notice ' + family, archive, expected=1, env=env)
        assert not candidate_zip.exists()
        write(notice, data)
    row = next(r for r in manifest['components'] if r['family'] == 'npm' and r['notices'])
    target = app / row['notices'][0]['destination']
    original = target.read_bytes()
    for label, data in [('empty', b''), ('whitespace', b' \n'), ('changed', b'not the original')]:
        write(target, data)
        call('case3 ' + label + ' notice', archive, expected=1, env=env)
        assert not candidate_zip.exists()
    write(target, original)
    target.chmod(0)
    call('case3 unreadable notice', archive, expected=1, env=env)
    target.chmod(0o644)
    for field, value in [('version', 'unrelated-version'), ('destination', '../../outside')]:
        broken = copy.deepcopy(manifest)
        changed = next(r for r in broken['components'] if r['id'] == row['id'])
        if field == 'version':
            changed[field] = value
        else:
            changed['notices'][0][field] = value
        write(manifest_path, broken)
        call('case3 mismatched identity or escaping destination ' + field, archive, expected=1, env=env)
        assert not candidate_zip.exists()
    write(manifest_path, manifest_raw)
    outside = scratch / 'outside'
    write(outside, original)
    target.unlink()
    target.symlink_to(outside)
    call('case3 symlink refuses without outside mutation', archive, expected=1, env=env)
    assert outside.read_bytes() == original and not candidate_zip.exists()
    target.unlink()
    write(target, original)
    packages = [r for r in manifest['components'] if r['component'] == 'react-remove-scroll-bar']
    assert len(packages) == 2 and len({r['version'] for r in packages}) == 2
    assert len({r['notices'][0]['sha256'] for r in packages}) == 1
    assert any('optional package not installed' in r['selection'] for r in manifest['components'])
    assert any('build-only' in r['selection'] for r in manifest['components'])
    vite_owner = next(r for r in manifest['components'] if r['component'] == 'vite')
    assert vite_owner['selection'].startswith('included:') and vite_owner['notices'], 'resolved generated-web tool must retain its original'
    assert len([r for r in manifest['components'] if r['family'] == 'asset' and 'Newsreader' in r['component']]) >= 2
    REPORT.append(dict(label='case4 multiple versions/shared authentic original/platform optional/build-only selections', exit=0, owners=len(manifest['components'])))
    plist_path = app / 'Contents/Info.plist'
    original_plist = plist_path.read_bytes()
    for key, value in [('CFBundleExecutable', 'Other'), ('CFBundlePackageType', 'BNDL'), ('CFBundleVersion', ''), ('LSMinimumSystemVersion', '13.0')]:
        changed = plistlib.loads(original_plist); changed[key] = value
        write(plist_path, plistlib.dumps(changed))
        call('case6 basic bundle rejects ' + key, archive, expected=1, env=env)
        assert not candidate_zip.exists()
    write(plist_path, original_plist)
    executable = app / 'Contents/MacOS/HappyRanch'
    executable_bytes = executable.read_bytes()
    write(executable, struct.pack('<IIIIIIII', 0xfeedfacf, 0x1000007, 0, 2, 0, 0, 0, 0))
    call('case6 actual x86_64 header refuses arm64', archive, expected=1, env=env)
    assert not candidate_zip.exists()
    write(executable, executable_bytes)
    resource = app / 'Contents/Resources/web/dist/index.html'
    resource_bytes = resource.read_bytes(); resource.unlink()
    call('case6 missing web entry refuses archive', archive, expected=1, env=env)
    assert not candidate_zip.exists()
    write(resource, resource_bytes)
    # Real failure status at the actual archive subprocess boundary, no supervisor.
    ditto_bytes = ditto.read_bytes()
    ditto.write_text('#!/bin/sh\nexit 19\n'); ditto.chmod(0o755)
    failed = call('case7 ordinary compressor status', archive, expected=1, env=env)
    assert failed.returncode == 19 and not candidate_zip.exists()
    ditto.write_text('#!/bin/sh\nkill -TERM $$\n'); ditto.chmod(0o755)
    failed = call('case7 compressor signal remains failure', archive, expected=1, env=env)
    assert failed.returncode != 0 and not candidate_zip.exists()
    ditto.write_bytes(ditto_bytes); ditto.chmod(0o755)
    call('case5 complete roundtrip', archive, env=env)
    call('case5 archived notices independent read', cli + ['archive', '--archive', str(candidate_zip)])
    notice_member = 'HappyRanch.app/' + row['notices'][0]['destination']
    truncated = scratch / 'truncated.zip'
    write(truncated, valid_zip.read_bytes()[:100])
    call('case5 truncated ZIP', cli + ['archive', '--archive', str(truncated)], expected=1)
    for mode in ('missing', 'changed'):
        tampered = scratch / (mode + '.zip')
        with zipfile.ZipFile(valid_zip) as src, zipfile.ZipFile(tampered, 'x') as dest:
            for entry in src.infolist():
                if entry.filename == notice_member and mode == 'missing':
                    continue
                dest.writestr(entry, b'changed original' if entry.filename == notice_member else src.read(entry))
        call('case5 ' + mode + ' archived notice', cli + ['archive', '--archive', str(tampered)], expected=1)
    verifier = ['bash', str(SOURCE / 'app/mac/scripts/verify-release.sh'), 'reconcile', '--archive', str(candidate_zip), '--receipt', str(output / 'diagnostics/inner-zip.json'), '--version', '1.0.1', '--build', '1.0.1', '--source-sha', '1' * 40, '--source-tree', '2' * 40, '--arch', 'arm64', '--run-id', 'portable-fixture']
    for option, value in [('--version', '2.0.0'), ('--build', '2.0.0'), ('--arch', 'x86_64'), ('--source-tree', '3' * 40)]:
        args = verifier.copy()
        args[args.index(option) + 1] = value
        call('case5 independent rejection ' + option, args, expected=1)
    REPORT.append(dict(label='limitations', native='UNEXECUTED', substitutions=['synthetic resolved manifests, installed RECORD, EXE.python_lib selection, standalone prefix and Analysis inputs; preassembled resources/plist/icon and synthetic arm64 Mach-O headers; no compiled native app', 'ditto replaced with Python zipfile'], actual='shipping collector, manifest validator, archive shell, resources, ZIP inventory and receipt reconciliation', integration='SKIPPED THR243seq42'))
    return scratch / 'evidence.json'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    parser.add_argument('--cpython-stdlib', type=Path, help='Pinned CPython stdlib sources and installed LICENSE.txt from the resolved archive; not a native interpreter')
    args = parser.parse_args()
    assert args.scratch.is_absolute() and not args.scratch.exists()
    try:
        evidence = run(args.scratch, args.cpython_stdlib)
    finally:
        write(args.scratch / 'evidence.json', REPORT)
    print(json.dumps(dict(cases='notice/archive/basic/status cases PASS portable only', evidence=str(evidence), commands=len(REPORT))))
