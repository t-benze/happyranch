"""Fixed ordinary-UID artifact stages; loaded only by the hosted coordinator.

No CLI, product imports, source overlay, arbitrary ref or command input.
Prepared stages are not evidence of successful builds or shipping behavior.
"""
from __future__ import annotations

import ast
import base64
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import zipfile


def receipt(path: Path) -> dict:
    assert path.is_absolute() and path.is_file() and not path.is_symlink()
    assert path.stat().st_uid == os.getuid() and not path.stat().st_mode & 0o022
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def bundle_inventory(root: Path) -> dict:
    """Same no-follow format as the existing driver, including the root."""
    rows = {}
    def visit(path: Path, relative: str) -> None:
        info = path.lstat()
        row = {'mode': stat.S_IMODE(info.st_mode), 'type': stat.S_IFMT(info.st_mode)}
        if stat.S_ISLNK(info.st_mode):
            row['link'] = os.readlink(path)
        elif stat.S_ISREG(info.st_mode):
            row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        elif stat.S_ISDIR(info.st_mode):
            for child in sorted(path.iterdir()):
                visit(child, relative + '/' + child.name)
        else:
            raise AssertionError('unsupported artifact entry')
        rows[relative] = row
    visit(root, '.')
    return rows


def wheel_inventory(wheel: Path) -> dict:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        for name in names:
            path = Path(name)
            assert not path.is_absolute() and '..' not in path.parts
        records = [name for name in names if name.endswith('.dist-info/RECORD')]
        assert len(records) == 1
        members = {name: hashlib.sha256(archive.read(name)).hexdigest()
                   for name in names if not name.endswith('/')}
        checked = []
        for name, encoded, size in csv.reader(archive.read(records[0]).decode().splitlines()):
            if not encoded:
                assert name == records[0]
                continue
            algorithm, value = encoded.split('=', 1)
            data = archive.read(name)
            assert algorithm == 'sha256' and len(data) == int(size)
            assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode() == value
            checked.append(name)
        assert set(checked) | set(records) == set(members)
    return {'wheel': receipt(wheel), 'members': members, 'record': records[0]}


INSTALLED_OBSERVER = r'''
import base64,csv,hashlib,importlib.metadata,json,pathlib,sys
d=importlib.metadata.distribution('happyranch');files=list(d.files or [])
r=[p for p in files if len(pathlib.PurePosixPath(str(p)).parts)==2 and str(p).endswith('.dist-info/RECORD')]
assert len(r)==1
record=pathlib.Path(d.locate_file(r[0]));site=record.parent.parent;checked={}
for name,encoded,size in csv.reader(record.read_text().splitlines()):
 p=pathlib.Path(d.locate_file(name))
 assert p.is_file() and not p.is_symlink()
 if not encoded:continue
 algorithm,value=encoded.split('=',1);assert algorithm=='sha256'
 data=p.read_bytes();assert len(data)==int(size)
 assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()==value
 checked[str(p)]={'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
pathlib.Path(sys.argv[1]).write_text(json.dumps({'site_packages':str(site),'record':str(record),
 'version':d.version,'executable':sys.executable,'installed_members':checked},indent=2)+'\n')
'''


def toc_inventory(work: Path, source: dict, save, label: str) -> Path:
    """Parse actual literal TOCs, never evaluate a spec or application module."""
    paths = sorted(work.rglob('*.toc'))
    assert paths and sum(p.stat().st_size for p in paths) <= 8 * 1024 * 1024
    values = {str(p.relative_to(work)): ast.literal_eval(p.read_text()) for p in paths}
    analyses = sorted(name for name in values if Path(name).name.startswith('Analysis-'))
    pyzs = sorted(name for name in values if Path(name).name.startswith('PYZ-'))
    assert len(analyses) == 2 and len(pyzs) == 1
    entries = []
    def walk(value) -> None:
        if isinstance(value, (list, tuple)):
            if len(value) == 3 and all(isinstance(x, str) for x in value):
                entries.append(value)
            else:
                for child in value:
                    walk(child)
    for value in values.values():
        walk(value)
    native = [row for row in entries if row[2] in ('BINARY', 'EXTENSION')]
    stdlib = [row for row in entries if row[2] in ('PYMODULE', 'PYMODULE-1', 'PYMODULE-2')
              and '/lib/python3.14/' in row[1] and '/site-packages/' not in row[1]]
    assert native and stdlib
    return save(label + '-build-tocs.json', {
        'candidate_sha': source['candidate_sha'], 'daemon_analysis': values[analyses[0]],
        'cli_analysis': values[analyses[1]], 'shared_pyz': values[pyzs[0]],
        'native_dependencies': native, 'python_stdlib': stdlib,
        'toc_files': {name: receipt(work / name) for name in values},
        'source_sha256': {name: source['files'][name] for name in ('cli/main.py', 'runtime/daemon/app.py')}})


def build_origin(origin: str, role: str, source: Path, source_record: Path,
                 stage: Path, commands, api: dict, uv: Path, python: Path,
                 freeze_env: Path, constraints: Path, descriptor: dict) -> Path:
    assert origin in ('wheel', 'frozen') and role in ('candidate', 'baseline')
    label = role + '-' + origin
    save, clean_env = api['save'], api['clean_env']
    receipts = api['receipts']
    stage.mkdir(mode=0o700)
    env = clean_env(stage / 'setup')
    env['PATH'] = str(uv.parent) + ':' + env['PATH']
    record = json.loads(source_record.read_text())
    tools = {'python_version': json.loads((receipts / 'python-runtime.json').read_text())['version'].split()[0],
             'uv_version': (receipts / 'shipping-uv-version.log').read_text().split()[1],
             'official_python_sha256': receipt(receipts / 'official-python-archive.json')['sha256'],
             'uv_distribution_sha256': receipt(receipts / 'uv-installed-records.json')['sha256'],
             'python_runtime': receipt(receipts / 'python-runtime.json'),
             'uv_records': receipt(receipts / 'uv-installed-records.json'),
             'hatch_records': receipt(receipts / 'hatch-installed-records.json'),
             'freeze_records': receipt(receipts / 'freeze-installed-records.json')}
    manifest = {'schema_version': 1, 'source_role': role, 'origin': origin,
        'candidate_sha': record['candidate_sha'], 'platform': sys.platform, 'arch': os.uname().machine,
        'source_digest': receipt(source_record)['sha256'], 'lock_digest': record['uv_lock_sha256'],
        'constraints_digest': receipt(constraints)['sha256'], 'source_manifest': receipt(source_record),
        'constraints': receipt(constraints), 'official_python_distribution': receipt(receipts / 'official-python-archive.json'),
        'uv_distribution': receipt(receipts / 'uv-installed-records.json'),
        'observer_python': str(python), 'observer_sha256': receipt(python.resolve())['sha256'],
        'native_observer': descriptor, 'path': str(python.parent) + ':/usr/bin:/bin:/usr/sbin:/sbin'}
    if origin == 'wheel':
        requirements = stage / 'runtime-requirements.txt'
        commands.run(label + '-export', [uv, 'export', '--frozen', '--no-dev', '--no-emit-project',
            '--format', 'requirements.txt', '--output-file', requirements, '--no-python-downloads', '--no-config'], source, env)
        env['UV_FIND_LINKS'] = str(api['wheels'])
        out = stage / 'wheel'
        commands.run(label + '-build', [uv, 'build', source, '--wheel', '--out-dir', out,
            '--python', python, '--no-python-downloads', '--build-constraints', constraints,
            '--require-hashes', '--no-config'], source, env, 300)
        wheels = sorted(out.glob('*.whl'))
        assert len(wheels) == 1
        wheel = wheels[0]
        save(label + '-archive.json', wheel_inventory(wheel))
        venv = stage / 'installed'
        commands.run(label + '-venv', [uv, 'venv', '--python', python, '--no-python-downloads', '--no-config', venv], stage, env)
        commands.run(label + '-deps', [uv, 'pip', 'install', '--python', venv / 'bin/python',
            '--require-hashes', '--no-build', '--no-python-downloads', '--no-config', '-r', requirements], stage, env, 300)
        commands.run(label + '-install', [uv, 'pip', 'install', '--python', venv / 'bin/python',
            '--no-deps', '--no-build', '--no-python-downloads', '--no-config', wheel], stage, env)
        installed = receipts / (label + '-installed.json')
        commands.run(label + '-record', [venv / 'bin/python', '-I', '-c', INSTALLED_OBSERVER, installed], stage, env)
        actual = json.loads(installed.read_text())
        site = Path(actual['site_packages'])
        assert site.is_relative_to(venv) and Path(actual['executable']) == venv / 'bin/python'
        native_files = [(venv / 'bin/python').resolve(), *(p for p in sorted(site.rglob('*'))
            if p.is_file() and not p.is_symlink() and ('.so' in p.name or p.suffix == '.dylib'))]
        assert len(native_files) <= 512
        commands.run(label + '-native', ['otool', '-L', *native_files] if sys.platform == 'darwin'
            else ['ldd', *native_files], stage, env, 120)
        manifest.update(site_packages=str(site), record=actual['record'],
            console=receipt(venv / 'bin/happyranch'), interpreter=receipt((venv / 'bin/python').resolve()),
            artifact=receipt(wheel), cli_argv=[str(venv / 'bin/happyranch')],
            daemon_argv=[str(venv / 'bin/python'), '-I', '-m', 'runtime.daemon'],
            skills_root=str(site / 'runtime/skills/bundled'))
        bundle = save(label + '-bundle.json', {'candidate_sha': record['candidate_sha'],
            'entries': bundle_inventory(venv), 'installed_record': receipt(installed),
            'archive_inventory': receipt(receipts / (label + '-archive.json')),
            'native': receipt(receipts / ('shipping-' + label + '-native.log'))})
    else:
        # The accepted spec writes its hook. Keep this in a closed attributed
        # build copy; never let it write candidate/baseline checkout bytes.
        copied = stage / 'build-source'
        copied.mkdir()
        for name, expected in record['files'].items():
            target = copied / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if name in record['links']:
                target.symlink_to(record['links'][name])
            else:
                shutil.copy2(source / name, target)
            assert hashlib.sha256(target.read_bytes()).hexdigest() == expected
        for name in record['links']:
            assert (copied / name).resolve(strict=True).is_relative_to(copied), 'build copy external link refused'
        save(label + '-build-copy.json', {'source': receipt(source_record), 'root': str(copied),
            'before': bundle_inventory(copied)})
        work, out = stage / 'work', stage / 'dist'
        env['VIRTUAL_ENV'] = str(freeze_env)
        commands.run(label + '-build', [uv, 'run', '--active', '--no-sync', '--frozen',
            '--python', freeze_env / 'bin/python', '--no-python-downloads', '--no-config',
            freeze_env / 'bin/pyinstaller', copied / 'packaging/daemon.spec', '--clean',
            '--noconfirm', '--workpath', work, '--distpath', out], copied, env, 600)
        bundle_root = out / 'happyranch-daemon'
        daemon, cli = bundle_root / 'happyranch-daemon', bundle_root / 'happyranch'
        archives = {}
        for name, executable in (('daemon', daemon), ('cli', cli)):
            log, _ = commands.run(label + '-' + name + '-archive', [freeze_env / 'bin/pyi-archive_viewer',
                '--list', '--recursive', '--brief', executable], stage, env)
            assert log.strip()
            archives[name] = receipt(receipts / ('shipping-' + label + '-' + name + '-archive.log'))
        native_files = [daemon, cli, *(p for p in sorted(bundle_root.rglob('*'))
            if p.is_file() and not p.is_symlink() and ('.so' in p.name or p.suffix == '.dylib'))]
        assert len(native_files) <= 512
        commands.run(label + '-native', ['otool', '-L', *native_files] if sys.platform == 'darwin'
            else ['ldd', *native_files], stage, env, 120)
        version, _ = commands.run(label + '-pyinstaller-version', [freeze_env / 'bin/pyinstaller', '--version'], stage, env)
        tools['pyinstaller_version'] = version.strip()
        toc = toc_inventory(work, record, save, label)
        bundle = save(label + '-bundle.json', {'candidate_sha': record['candidate_sha'],
            'entries': bundle_inventory(bundle_root), 'native': receipt(receipts / ('shipping-' + label + '-native.log'))})
        artifact = save(label + '-artifact.json', {'bundle': receipt(bundle),
            'executables': [receipt(daemon), receipt(cli)], 'archives': archives, 'tocs': receipt(toc)})
        manifest.update(bundle_root=str(bundle_root), artifact=receipt(artifact),
            daemon_argv=[str(daemon)], cli_argv=[str(cli)], executables=[receipt(daemon), receipt(cli)],
            daemon_archive=archives['daemon'], cli_archive=archives['cli'], build_tocs=receipt(toc),
            native_os_receipt={'version': os.uname().release,
                'process_api': 'libproc' if sys.platform == 'darwin' else 'procfs',
                'admission': descriptor}, skills_root=str(bundle_root / '_internal/runtime/skills/bundled'))
    manifest['bundle_manifest'] = receipt(bundle)
    manifest['tool_record'] = receipt(save(label + '-tools.json', tools))
    return save(label + '-manifest.json', manifest)


def run_cases(origin: str, role: str, manifest: Path, stage: Path, commands,
              api: dict, python: Path, driver: Path, descriptor: dict, source: Path) -> list:
    env = api['clean_env'](stage / 'behavior-env')
    cases = ('lifecycle', 'parser', 'ordinary-callback', 'nonrunning-swap') if role == 'candidate' else ('ordinary-callback', 'nonrunning-swap')
    outcomes = []
    for case in cases:
        label = role + '-' + origin + '-' + case
        api['census'](commands, label + '-before', python, driver, descriptor,
            stage.parent, env, source, stage)
        result = api['receipts'] / (label + '-behavior.json')
        _, code = commands.run(label, [python, '-I', driver, 'run', '--origin', origin,
            '--origin-manifest', manifest, '--run-root', stage / ('run-' + case),
            '--cases', case, '--deadline-seconds', '240', '--receipt-json', result],
            stage, env, 1000, required=False)
        outcome = {'case': case, 'exit': code, 'receipt': receipt(result) if result.is_file() else None,
                   'error': commands.rows[-1].get('error')}
        outcomes.append(outcome)
        api['save'](role + '-' + origin + '-outcomes.json', outcomes)
        api['census'](commands, label + '-after', python, driver, descriptor,
            stage.parent, env, source, stage)
    return outcomes


def artifact_stage(commands, candidate: Path, baseline: Path, root: Path,
                   uv: Path, python: Path, freeze_env: Path, constraints: Path,
                   descriptor: dict, api: dict) -> dict:
    assert os.getuid() == os.geteuid() != 0
    driver_root = root / 'artifact-driver'
    driver_root.mkdir(mode=0o700)
    for name in ('assistant_retirement_artifact_driver.py', 'assistant_retirement_native_observer.c'):
        shutil.copyfile(candidate / 'tests/helpers' / name, driver_root / name)
        assert receipt(driver_root / name)['sha256'] == receipt(candidate / 'tests/helpers' / name)['sha256']
    api['save']('artifact-driver-copy.json', {p.name: receipt(p) for p in driver_root.iterdir()})
    outcomes = {}
    for origin in ('wheel', 'frozen'):
        for role, source in (('candidate', candidate), ('baseline', baseline)):
            label = role + '-' + origin
            stage = root / label
            outcome = {'status': 'failed'}
            outcomes[label] = outcome
            try:
                source_record = api['receipts'] / (role + '-after-source-manifest.json')
                manifest = build_origin(origin, role, source, source_record, stage, commands,
                    api, uv, python, freeze_env, constraints, descriptor)
                outcome['manifest'] = receipt(manifest)
                outcome['cases'] = run_cases(origin, role, manifest, stage, commands, api,
                    python, driver_root / 'assistant_retirement_artifact_driver.py', descriptor, source)
                if all(row['exit'] == 0 and row['receipt'] and not row['error'] for row in outcome['cases']):
                    outcome['status'] = 'passed'
            except Exception as error:
                outcome['error'] = {'type': type(error).__name__, 'message': str(error)}
            finally:
                api['save']('artifact-outcomes.json', outcomes)
            # Opaque rows or live source/stage residue stop further execution.
            api['census'](commands, label + '-final', python,
                driver_root / 'assistant_retirement_artifact_driver.py', descriptor,
                root, api['clean_env'](root / (label + '-final-env')), source, stage)
    return outcomes
