"""Finite collection-environment acquisition, without pytest/product imports.

Installed plugin sources must be reviewed before collection is admitted. This
helper supplies their actual installed identity; it does not execute collection.
"""
from __future__ import annotations

from pathlib import Path


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
