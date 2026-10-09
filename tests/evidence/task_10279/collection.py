"""Finite installed-source acquisition and audited collect-only coordination.

Acquisition does not import pytest/product code. Execution requires the reviewed
source/plugin hashes and early controls, and retains actual collection failures.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import json


INSTALLED_SOURCES = r'''
import base64, csv, hashlib, importlib.metadata, io, json, os, pathlib, stat, sys, sysconfig, zipfile
assert sys.version_info[:3] == (3, 14, 4)
assert os.getuid() == os.geteuid() != 0
assert sys.flags.isolated and sys.flags.no_site and 'site' not in sys.modules
destination = pathlib.Path(sys.argv[1])
archive = pathlib.Path(sys.argv[2])
assert not destination.exists() and not archive.exists()
root = pathlib.Path(sysconfig.get_path('purelib')).resolve(strict=True)
assert root.is_relative_to(pathlib.Path(sys.prefix).resolve(strict=True))
rows = {}
copied_bytes = 0
with zipfile.ZipFile(archive, 'x', compression=zipfile.ZIP_DEFLATED) as z:
 for path in sorted(root.rglob('*')):
  assert not path.is_symlink(), str(path)
  if path.is_dir(): continue
  info = path.stat()
  assert stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
  assert not info.st_mode & 0o022
  if '__pycache__' in path.parts: continue
  relative = str(path.relative_to(root))
  data = path.read_bytes()
  rows[relative] = {'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
  assert len(rows) <= 20000
  if path.suffix in ('.py', '.pth') or path.name in ('METADATA', 'RECORD', 'entry_points.txt', 'pyvenv.cfg'):
   copied_bytes += len(data)
   assert copied_bytes <= 64 * 1024 * 1024
   z.writestr(relative, data)
assert archive.stat().st_size <= 64 * 1024 * 1024
distributions = []
plugins = []
for d in sorted(importlib.metadata.distributions(path=[str(root)]), key=lambda d: d.metadata['Name'].lower()):
 item = {'name': d.metadata['Name'], 'version': d.version,
         'entry_points': [{'group': e.group, 'name': e.name, 'value': e.value} for e in d.entry_points]}
 distributions.append(item)
 record = d.read_text('RECORD')
 assert record is not None, item['name']
 verified = 0
 for relative, encoded, size in csv.reader(io.StringIO(record)):
  if not encoded: continue
  path = pathlib.Path(d.locate_file(relative)).resolve(strict=True)
  assert path.is_relative_to(pathlib.Path(sys.prefix).resolve(strict=True))
  data = path.read_bytes()
  algorithm, expected = encoded.split('=', 1)
  assert algorithm == 'sha256' and len(data) == int(size)
  assert base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode() == expected
  verified += 1
 item['verified_record_members'] = verified
 plugins += [{'distribution': item['name'], 'version': item['version'],
              'name': e.name, 'value': e.value} for e in d.entry_points if e.group == 'pytest11']
assert not any(n == 'pytest' or n.startswith(('_pytest', 'pytest_asyncio', 'xdist', 'runtime', 'cli', 'tests.')) for n in sys.modules)
customization = [p for p in rows if pathlib.PurePosixPath(p).name in ('sitecustomize.py', 'usercustomize.py') or p.endswith('.pth')]
destination.write_text(json.dumps({'scope': 'installed metadata/source acquisition only; no plugin startup, pytest or candidate imports',
 'collection_executed': False, 'collection_admitted': False,
 'python': {'executable': sys.executable, 'version': sys.version, 'prefix': sys.prefix},
 'uid': os.getuid(), 'purelib': str(root), 'files': rows,
 'distributions': distributions, 'pytest11_entry_points': plugins,
 'startup_customization_candidates': customization,
 'plugins_loaded': False, 'site_startup_disabled': True,
 'source_archive': {'path': str(archive), 'bytes': archive.stat().st_size,
 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest(), 'expanded_bytes': copied_bytes},
 'environment': dict(os.environ),
 'imported_modules': sorted(sys.modules)}, indent=2, sort_keys=True) + '\n')
'''


def acquire(commands, source: Path, root: Path, env: dict[str, str], uv: Path,
            python: Path, descriptor: dict, observer: Path, api: dict) -> dict:
    """Use the existing locked source setup and admitted native cleanup."""
    stage = root / 'collection-environment'
    setup = api['clean_env'](stage / 'setup')
    venv = stage / 'installed'
    setup['VIRTUAL_ENV'] = setup['UV_PROJECT_ENVIRONMENT'] = str(venv)
    setup['PATH'] = str(uv.parent) + ':' + setup['PATH']
    census = api['census']
    result = {'status': 'failed', 'collection_admitted': False,
              'collection_executed': False, 'test_bodies_executed': False,
              'scope': 'environment acquisition only; effective plugin hooks and full audit remain pending'}
    try:
        census(commands, 'collection-acquisition-before', python, observer, descriptor,
               root, env, source, stage)
        commands.run('collection-venv', [uv, 'venv', '--python', python,
                     '--no-python-downloads', '--no-config', venv], source, setup)
        commands.run('collection-sync', [uv, 'sync', '--active', '--frozen',
                     '--no-install-project', '--no-install-local', '--no-build',
                     '--python', python, '--no-python-downloads', '--no-config'],
                     source, setup, 300)
        destination = api['receipts'] / 'collection-installed-sources.json'
        archive = api['receipts'] / 'collection-installed-sources.zip'
        commands.run('collection-installed-source-acquisition',
                     [venv / 'bin/python', '-I', '-S', '-c', INSTALLED_SOURCES, destination, archive],
                     source, setup, 120)
        result['installed_sources'] = api['identity'](destination)
        result['installed_source_archive'] = api['identity'](archive)
        result['status'] = 'sources-acquired-cleanup-pending'
        return result
    finally:
        # This census follows Commands.run's bounded group TERM/KILL/wait/reap.
        # Opaque rows/survivors fail and prevent a clean acquisition claim.
        census(commands, 'collection-acquisition-after', python, observer, descriptor,
               root, env, source, stage)
        result['cleanup'] = 'complete-native-census-no-owned-survivors'
        if result['status'] == 'sources-acquired-cleanup-pending':
            result['status'] = 'acquired-collection-held'
        api['save']('collection-acquisition.json', result)


STDLIB_BINDING = r'''
import ast, hashlib, json, os, pathlib, sys, sysconfig
audit=json.loads(pathlib.Path(sys.argv[1]).read_text())
root=pathlib.Path(sysconfig.get_path('stdlib')).resolve(strict=True)
rows={}
for name,expected in audit['stdlib_hashes'].items():
 p=root/name
 assert p.is_file() and hashlib.sha256(p.read_bytes()).hexdigest()==expected, name
 rows[str(p)]=expected
# Native stdlib extensions are freshly built from the same official pinned
# CPython archive/recipe, not compared to another build's physical bytes.
native={}
for p in sorted((root/'lib-dynload').glob('*.so')):
 assert p.is_file() and not p.is_symlink()
 h=hashlib.sha256(p.read_bytes()).hexdigest()
 rows[str(p)]=h
 native[str(p)]=h
generated={}
for p in sorted(root.glob('_sysconfigdata*.py')):
 blob=p.read_bytes(); tree=ast.parse(blob)
 for n in tree.body:
  assert isinstance(n,ast.Assign), ast.dump(n)
  assert all(isinstance(t,ast.Name) for t in n.targets)
  ast.literal_eval(n.value)
 h=hashlib.sha256(blob).hexdigest(); rows[str(p)]=h; generated[str(p)]=h
assert generated
pathlib.Path(sys.argv[2]).write_text(json.dumps({'files':rows,'stdlib':str(root),
 'native_extensions':native,'generated_literal_configuration':generated,
 'python':{'executable':sys.executable,'prefix':sys.prefix,'version':sys.version},
 'uid':os.getuid(),'candidate_imports':False},sort_keys=True,indent=2)+'\n')
'''


def execute(commands, source: Path, root: Path, env: dict[str, str], uv: Path,
            python: Path, descriptor: dict, observer: Path, api: dict) -> dict:
    """Fresh hash/option admission, source-parent collection, actual cleanup."""
    acquired = acquire(commands, source, root, env, uv, python, descriptor, observer, api)
    stage = root / 'collection-environment'
    venv = stage / 'installed'
    audit_path = Path(__file__).with_name('collection-audit.json')
    control = Path(__file__).with_name('collection_control.py')
    audit = json.loads(audit_path.read_text())
    assert audit['candidate'] == '294beab846efbceecc3fa5dbfb77ff40c95fa5af'
    installed = json.loads((api['receipts'] / 'collection-installed-sources.json').read_text())
    # All known installed code/startup bytes, including conditionally excluded
    # code, must match before selecting the actual executable allow corpus.
    assert {p:r['sha256'] for p,r in installed['files'].items() if p.endswith(('.py','.pth'))} == audit['installed_all_python_hashes']
    assert installed['pytest11_entry_points'] == audit['pytest11']
    assert [[d['name'],d['version']] for d in installed['distributions']] == audit['distributions']
    assert installed['startup_customization_candidates'] == ['_virtualenv.pth']
    assert not [e for d in installed['distributions'] for e in d['entry_points']
                if e['group'] in ('pydantic','pygments.lexers','pygments.styles','pygments.filters','pygments.formatters')]
    for name, expected in audit['source_hashes'].items():
        assert hashlib.sha256((source / name).read_bytes()).hexdigest() == expected, name
    environment = api['clean_env'](stage / 'parent-setup')
    environment['PATH'] = str(uv.parent) + ':' + environment['PATH']
    environment['VIRTUAL_ENV'] = environment['UV_PROJECT_ENVIRONMENT'] = str(venv)
    environment['UV_PYTHON'] = str(venv / 'bin/python')
    environment['HAPPYRANCH_TEST_NATIVE_OBSERVER_RECEIPT'] = json.dumps({
        'path': str(api['receipts'] / 'native-descriptor.json'),
        'sha256': hashlib.sha256((api['receipts'] / 'native-descriptor.json').read_bytes()).hexdigest()})
    stdlib_path = api['receipts'] / 'collection-stdlib-binding.json'
    commands.run('collection-stdlib-binding', [venv / 'bin/python', '-I', '-S', '-c',
                 STDLIB_BINDING, audit_path, stdlib_path], source, environment, 120)
    stdlib = json.loads(stdlib_path.read_text())
    verified = {str((source / p).resolve()): h for p,h in audit['source_hashes'].items()}
    purelib = Path(installed['purelib'])
    for path, expected in audit['installed_hashes'].items():
        assert installed['files'][path]['sha256'] == expected, path
        verified[str((purelib / path).resolve())] = expected
    verified.update(stdlib['files'])
    verified[str(control.resolve())] = hashlib.sha256(control.read_bytes()).hexdigest()
    cfg = {'candidate': audit['candidate'], 'source': str(source), 'stage': str(stage),
           'audit': str(audit_path), 'verified_files': verified,
           'receipt_root': str(api['receipts']),
           'parent_receipt': str(api['receipts'] / 'collection-parent.json'),
           'child_receipt': str(api['receipts'] / 'collection-child.json'),
           'events': str(api['receipts'] / 'collection-controls.jsonl'),
           # Complete four non-test families in pinned CPython3.14.4's
           # Tools/build/freeze_modules.py; source hashes bind every member.
           # Frozen test fixtures and unknown code origins remain excluded.
           'frozen_code_names': ['<frozen ' + n + '>' for n in (
               'importlib._bootstrap','importlib._bootstrap_external','zipimport','abc','codecs',
               'io','_collections_abc','_sitebuiltins','os','site','stat','importlib.util','importlib.machinery',
               'ntpath','posixpath','genericpath','runpy')],
           'native_runtime': api['identity'](api['receipts'] / 'python-runtime.json')}
    config_path = api['save']('collection-config.json', cfg)
    result = {'status': 'failed', 'source': audit['candidate'], 'scope': 'whole-repository import/discovery only',
              'is_behavioral_or_keeper_pass': False, 'acquisition': acquired,
              'audit': api['identity'](audit_path), 'control': api['identity'](control),
              'selection': ['pytest','tests/','-v','-m','','--collect-only'], 'collection_executed': False}
    census = api['census']
    try:
        census(commands, 'collection-before', python, observer, descriptor, root, env, source, stage)
        result['collection_attempted'] = True
        result['collection_executed'] = None  # Actual child receipt decides.
        _, code = commands.run('whole-repository-collect-only',
            [venv / 'bin/python', '-S', control, '--parent', config_path],
            source, environment, 300, required=False)
        result['exit'] = code
        child = Path(cfg['child_receipt'])
        if child.is_file():
            data = json.loads(child.read_text())
            result['collection_executed'] = data['state']['sessionstarted']
            result['child_receipt'] = api['identity'](child)
            result['collected'] = data['state'].get('testscollected')
            result['selected'] = len(data['state']['items'])
            result['deselected'] = data['state']['deselected']
            result['errors'] = data['state'].get('collection_errors')
            result['controls'] = data['controls']
            result['denied'] = data['denied']
            result['ip_probes'] = data['probes']
            if code == 0:
                assert data['state']['sessionstarted'] and data['profile_retained']
                assert result['collected'] == result['selected'] > 0
                assert result['deselected'] == result['errors'] == 0
                assert not data['denied']
                assert all(data['controls'][k] == 0 for k in (
                    'fixture_entries','test_body_entries','runtest_entries','provider_entries'))
                assert {p['source'] for p in data['probes']} == set(audit['ip_sources'])
                assert len(data['probes']) == 3
                assert all(p['error_type'] == 'FileNotFoundError' and p['errno'] == 2
                           and p['native_fork_child_reaped'] for p in data['probes'])
                assert data['audit_event_counts'].get('subprocess.Popen') == 3
            result['status'] = 'collected' if code == 0 else 'collection-errors-retained'
        else:
            result['status'] = 'collection-refused-no-complete-child-receipt'
        return result
    finally:
        try:
            census(commands, 'collection-after', python, observer, descriptor, root, env, source, stage)
            result['cleanup'] = 'complete-native-table-no-owned-survivors-after-parent-group-reap'
        except BaseException as error:
            result['cleanup'] = 'failed-or-unavailable'
            result['cleanup_error'] = {'type': type(error).__name__, 'message': str(error)}
            raise
        finally:
            api['save']('whole-repository-collection.json', result)
