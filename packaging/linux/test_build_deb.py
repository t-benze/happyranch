"""Focused native packaging checks through the real dpkg-deb boundary."""
from __future__ import annotations

import ast
import base64
import gzip
import hashlib
import io
import json
import os
import pwd
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, Iterator
import runpy
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zlib
from unittest.mock import patch

BUILDER = Path(__file__).with_name('build-deb.sh')
UNIT = BUILDER.parent / 'systemd/happyranch-daemon.service'


class RetirementPreinstTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='retirement-preinst-')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.generator = runpy.run_path(str(BUILDER.with_name('retirement-preinst.py')))
        self.script = self.work / 'preinst'
        self.script.write_text(self.generator['render_preinst'](BUILDER.parents[2]))
        self.entry = runpy.run_path(str(self.script))

    def test_inline_bundle_parity_and_closed_imports(self) -> None:
        sources = json.loads(zlib.decompress(base64.b64decode(self.entry['SOURCES'])))
        self.assertEqual(set(sources), {'runtime.remote_access.' + name for name in self.generator['MODULES']})
        for name, data in sources.items():
            self.assertEqual(data.encode(), (BUILDER.parents[2] / (name.replace('.', '/') + '.py')).read_bytes())
        for name in ('installer', 'home_activation', 'home_admission', 'home_admin', 'home_minter', 'home_headscale'):
            with self.subTest(owner=name), self.assertRaises(ModuleNotFoundError):
                self.entry['InlineSources']().find_spec('runtime.remote_access.' + name)
        # Execute actual incoming module bytes under isolated system Python.
        # This fresh read-only closure must not need Pydantic or old runtime.
        program = '''import runpy, sys
entry = runpy.run_path(sys.argv[1])
sys.meta_path.insert(0, entry['InlineSources']())
from runtime.remote_access.home_retirement import HomeRetirement
from runtime.remote_access.retirement_io import RetirementIO
assert HomeRetirement.__module__ == 'runtime.remote_access.home_retirement'
assert sys.modules[HomeRetirement.__module__].__file__.startswith('<incoming-preinst:')
assert not any(name in sys.modules for name in ('pydantic', 'runtime.remote_access.installer'))
'''
        result = subprocess.run(['/usr/bin/python3', '-I', '-c', program, str(self.script)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_fresh_install_uses_canonical_detector_without_old_interpreter(self) -> None:
        for case in ('fresh', 'legacy', 'malformed', 'retired', 'drift', 'retired-sync-failure'):
            with self.subTest(case=case):
                root = self.work / case
                root.mkdir(mode=0o755)
                if case in ('legacy', 'malformed'):
                    target = root / ('etc/happyranch/invite-issuer.json' if case == 'legacy' else
                                     'var/lib/happyranch-remote-access/home-retirement.json')
                    target.parent.mkdir(parents=True)
                    for directory in root.rglob('*'):
                        if directory.is_dir():
                            directory.chmod(0o755)
                    target.write_text('synthetic-private-evidence')
                    target.chmod(0o600)
                    if case == 'malformed':
                        target.parent.chmod(0o700)
                program = '''import os, runpy, sys
from pathlib import Path
from unittest.mock import patch
entry = runpy.run_path(sys.argv[1])
sys.meta_path.insert(0, entry['InlineSources']())
from runtime.remote_access import retirement_io
from runtime.remote_access.home_retirement import _ABSENT_UNIT, HomeRetirement, STATE_DIRECTORY
from runtime.remote_access.home_retirement_fence import retirement_fence
from runtime.remote_access.home_retirement_publication import publish_final
root = Path(sys.argv[2])
def absent(name):
    raise KeyError(name)
calls = []
def native(command, **kwargs):
    calls.append(command)
    assert command[:3] == ['systemctl', 'show', 'happyranch-headscale.service']
    return 0, '\\n'.join(key + '=' + value for key, value in _ABSENT_UNIT.items())
io = retirement_io.RetirementIO(root=root, root_owner=(os.getuid(), os.getgid()),
    run=native, lookup_user=absent, lookup_group=absent)
if sys.argv[3] in ('retired', 'drift', 'retired-sync-failure'):
    state = root / STATE_DIRECTORY
    state.mkdir(parents=True)
    for directory in root.rglob('*'):
        if directory.is_dir():
            directory.chmod(0o700 if directory == state else 0o755)
    with retirement_fence(io) as fence:
        publish_final(io, fence, require=lambda: 'absent')
    assert HomeRetirement(io).detect().state == 'retired'
    if sys.argv[3] == 'drift':
        target = root / 'etc/happyranch/invite-issuer.json'
        target.parent.mkdir(parents=True)
        for directory in (target.parent, target.parent.parent):
            directory.chmod(0o755)
        target.write_text('synthetic-private-evidence')
        target.chmod(0o600)
def snapshot() -> dict[str, tuple]:
    result = {}
    for path in root.rglob('*'):
        info = path.lstat()
        result[str(path.relative_to(root))] = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns, path.read_bytes() if path.is_file() else None)
    return result
before = snapshot()
def no_old_python():
    raise AssertionError('fresh detection must not require old capability')
entry['main'].__globals__['trusted_python'] = no_old_python
case = sys.argv[3]
def sync_failure(fd: int) -> None:
    raise OSError('synthetic final confirmation failure')
for arguments in (['preinst', 'install'], ['preinst', 'install', 'old-version', 'new-version']):
    with patch.object(retirement_io, 'RetirementIO', return_value=io), patch('os.geteuid', return_value=0), patch.object(sys, 'argv', arguments):
        if case == 'retired-sync-failure':
            with patch('os.fsync', sync_failure):
                result = entry['main']()
        else:
            result = entry['main']()
    assert result == (0 if case in ('fresh', 'retired') else 1), (case, arguments, result)
    assert snapshot() == before
assert all(command[:3] == ['systemctl', 'show', 'happyranch-headscale.service'] for command in calls)
'''
                result = subprocess.run(['/usr/bin/python3', '-I', '-c', program, str(self.script), str(root), case],
                                        text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                states = [json.loads(line)['state'] for line in result.stdout.splitlines() if line.startswith('{')]
                self.assertEqual(states, [case if case in ('fresh', 'retired') else 'migration pending'] * 2)
                self.assertNotIn('synthetic-private', result.stdout + result.stderr)

    def test_installed_python_custody_refusals(self) -> None:
        tree = self.work / 'python'
        (tree / 'bin').mkdir(parents=True)
        tree.chmod(0o755)
        (tree / 'bin').chmod(0o755)
        executable = tree / 'bin/python3.12'
        executable.write_text('synthetic interpreter; never executed')
        executable.chmod(0o755)
        python = tree / 'bin/python3'
        python.symlink_to('python3.12')
        self.entry['trusted_python'].__globals__['PYTHON'] = python
        actual_lstat = Path.lstat
        foreign = set()
        def owned(path: Path, *args: Any, **kwargs: Any) -> SimpleNamespace:
            info = actual_lstat(path, *args, **kwargs)
            return SimpleNamespace(st_uid=1000 if path in foreign else 0, st_gid=0, st_nlink=info.st_nlink,
                st_mode=info.st_mode if path.is_relative_to(tree) else stat.S_IFDIR | 0o755)
        with patch.object(Path, 'lstat', owned):
            self.entry['trusted_python']()
            for path in (tree.parent, executable):
                foreign.add(path)
                with self.subTest(foreign=path.name), self.assertRaises(ValueError):
                    self.entry['trusted_python']()
                foreign.clear()
            executable.chmod(0o777)
            with self.assertRaises(ValueError):
                self.entry['trusted_python']()
            executable.chmod(0o755)
            extra = tree / 'extra'
            os.link(executable, extra)
            with self.assertRaises(ValueError):
                self.entry['trusted_python']()
            extra.unlink()
            extra.symlink_to(self.script)
            with self.assertRaises(ValueError):
                self.entry['trusted_python']()
            extra.unlink()
            python.unlink()
            python.symlink_to('missing')
            with self.assertRaises((ValueError, OSError)):
                self.entry['trusted_python']()

    def test_upgrade_gate_propagates_child_failure_and_pins_capability(self) -> None:
        main = self.entry['main']
        seen = []
        def child(command: list[str], **kwargs: Any) -> SimpleNamespace:
            seen.append((command, kwargs))
            return SimpleNamespace(returncode=37)
        with patch('os.geteuid', return_value=0), patch.object(sys, 'argv', ['preinst', 'upgrade', 'old']), \
                patch.dict(main.__globals__, trusted_python=lambda: None), patch('subprocess.run', side_effect=child):
            self.assertEqual(main(), 37)
        self.assertEqual(len(seen), 1)
        command, options = seen[0]
        self.assertEqual(command, ['/opt/happyranch-desktop/python/bin/python3', '-I', '-'])
        self.assertEqual(options['env'], {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})
        # The real capability program must refuse the system interpreter (not
        # bundled CPython 3.12); no canonical destructive owner is reached.
        failed = subprocess.run(['/usr/bin/python3', '-I', '-'], input=options['input'], text=True, capture_output=True)
        self.assertEqual(failed.returncode, 1, failed.stderr)
        self.assertIn('migration pending', failed.stdout)
        with patch('os.geteuid', return_value=0), patch.object(sys, 'argv', ['preinst', 'upgrade']), \
                patch.dict(main.__globals__, trusted_python=lambda: (_ for _ in ()).throw(ValueError())), \
                patch('subprocess.run') as execute:
            self.assertEqual(main(), 1)
            execute.assert_not_called()


class OwnerBootstrapTests(unittest.TestCase):
    @contextmanager
    def fixture(self) -> Iterator[SimpleNamespace]:
        # Simulate root ownership and the trusted physical prefix of this
        # injected filesystem. Fixture modes, no-follow opens, inode/link checks
        # and reads still execute against real temporary files.
        with tempfile.TemporaryDirectory(prefix='helper-owner-', dir='/tmp') as temporary:
            root = Path(temporary)
            row = pwd.struct_passwd(('owner', 'x', 1000, 1000, '', '/home/owner', '/bin/sh'))
            candidate = root / 'home/owner/.local/share/happyranch'
            candidate.mkdir(parents=True)
            record = root / 'etc/happyranch/release-daemon.json'
            record.parent.mkdir(parents=True)
            for directory in root.rglob('*'):
                if directory.is_dir():
                    directory.chmod(0o755)
            prefix_inodes = {parent.stat().st_ino for parent in root.parents}
            owned = {candidate.stat().st_ino}
            fixture = SimpleNamespace(root=root, row=row, rows=[row], candidate=candidate,
                                      record=record, owned=owned)
            fixture.passwd = SimpleNamespace(getpwall=lambda: fixture.rows)
            actual_fstat = os.fstat
            def metadata(fd: int) -> SimpleNamespace:
                info = actual_fstat(fd)
                values = {key: getattr(info, key) for key in dir(info) if key.startswith('st_')}
                values['st_uid'] = 1000 if info.st_ino in owned else 0
                # The physical test prefix is outside the injected filesystem.
                if info.st_ino in prefix_inodes:
                    values['st_mode'] &= ~0o022
                return SimpleNamespace(**values)
            with patch('os.fstat', side_effect=metadata), patch('pwd.getpwuid', return_value=row):
                yield fixture

    @staticmethod
    def owner_module() -> dict[str, Any]:
        return runpy.run_path(str(BUILDER.parents[2] / 'runtime/remote_access/helper_owner.py'))

    def resolve(self, fixture: SimpleNamespace, uid: int = 1000) -> Any:
        return self.module['bootstrap_owner'](uid, passwd=fixture.passwd, fs_root=fixture.root)

    def setUp(self) -> None:
        self.module = self.owner_module()

    @staticmethod
    def write_record(fixture: SimpleNamespace, **changes: Any) -> None:
        record = dict(user='owner', uid=1000, data_home='/home/owner/.local/share/happyranch',
                      unit='happyranch-daemon.service',
                      fragment_path='/usr/lib/systemd/user/happyranch-daemon.service')
        record.update(changes)
        fixture.record.write_text(json.dumps(record))
        fixture.record.chmod(0o644)

    def refused(self, fixture: SimpleNamespace, code: str, uid: int = 1000) -> None:
        with self.assertRaises(self.module['OwnerRefused']) as raised:
            self.resolve(fixture, uid)
        self.assertEqual(str(raised.exception), code)
        self.assertEqual(raised.exception.category, code)

    def test_bootstrap_passwd_and_uid_refusals(self) -> None:
        for case, count, uid, code in (
            ('unknown', 0, 1000, 'owner_unknown'),
            ('ambiguous', 2, 1000, 'owner_ambiguous'),
            ('root', 1, 0, 'owner_unsupported'),
            ('system', 1, 999, 'owner_unsupported'),
        ):
            with self.subTest(case=case), self.fixture() as fixture:
                fixture.rows = [fixture.row] * count
                self.refused(fixture, code, uid)

    def test_bootstrap_default_and_record_owner(self) -> None:
        for use_record in (False, True):
            with self.subTest(record=use_record), self.fixture() as fixture:
                expected = Path('/home/owner/.local/share/happyranch')
                if use_record:
                    expected = Path('/home/owner/release-data')
                    custom = fixture.root / expected.relative_to('/')
                    custom.mkdir(mode=0o700)
                    fixture.owned.add(custom.stat().st_ino)
                    self.write_record(fixture, data_home=str(expected))
                with patch.dict(os.environ, XDG_DATA_HOME='/untrusted', HOME='/untrusted'):
                    owner = self.resolve(fixture)
                self.assertEqual((owner.uid, owner.data_home), (1000, expected))
                self.assertEqual(fixture.record.exists(), use_record, 'bootstrap must never write a record')

    def test_bootstrap_record_owner_mismatch(self) -> None:
        for changes in (dict(uid=1001), dict(user='another')):
            with self.subTest(changes=changes), self.fixture() as fixture:
                self.write_record(fixture, **changes)
                self.refused(fixture, 'owner_mismatch')

    def test_bootstrap_invalid_evidence(self) -> None:
        for case in ('missing', 'home_symlink', 'directory_symlink', 'group_writable',
                     'world_writable', 'foreign_owner', 'dev_home', 'root_home',
                     'etc_home', 'var_home', 'record_symlink', 'record_hardlink',
                     'record_writable', 'record_extra_key', 'record_parent_writable',
                     'record_parent_symlink'):
            with self.subTest(case=case), self.fixture() as fixture:
                if case == 'missing':
                    fixture.candidate.rmdir()
                elif case == 'home_symlink':
                    home = fixture.root / 'home/owner'
                    home.rename(home.with_name('target'))
                    home.symlink_to('target', target_is_directory=True)
                elif case == 'directory_symlink':
                    fixture.candidate.rename(fixture.candidate.with_name('target'))
                    fixture.candidate.symlink_to('target', target_is_directory=True)
                elif case in ('group_writable', 'world_writable'):
                    fixture.candidate.chmod(0o770 if case == 'group_writable' else 0o707)
                elif case == 'foreign_owner':
                    fixture.owned.clear()
                else:
                    self.write_record(fixture)
                    if case in ('dev_home', 'root_home', 'etc_home', 'var_home'):
                        self.write_record(fixture, data_home={
                            'dev_home': '/home/owner/.happyranch/release', 'root_home': '/',
                            'etc_home': '/etc/release', 'var_home': '/var/release'}[case])
                    elif case == 'record_symlink':
                        fixture.record.rename(fixture.record.with_name('target'))
                        fixture.record.symlink_to('target')
                    elif case == 'record_hardlink':
                        os.link(fixture.record, fixture.record.with_name('alias'))
                    elif case == 'record_writable':
                        fixture.record.chmod(0o666)
                    elif case == 'record_extra_key':
                        self.write_record(fixture, untrusted='not allowed')
                    elif case == 'record_parent_writable':
                        fixture.record.parent.chmod(0o777)
                    elif case == 'record_parent_symlink':
                        parent = fixture.record.parent
                        parent.rename(parent.with_name('target'))
                        parent.symlink_to('target', target_is_directory=True)
                self.refused(fixture, 'release_home_invalid')


class NoticeCollectionTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='notice-test-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.source = self.work / 'source'
        self.shared = self.source / 'app/shared/tsnethealth'
        self.shared.mkdir(parents=True)
        self.outside = self.work / 'outside'
        self.outside.mkdir()
        repo = BUILDER.parents[2]
        for name in ('LICENSE', 'packaging/linux/python-build-standalone-LICENSE.txt',
                     'packaging/linux/THIRD_PARTY_NOTICES.md', 'packaging/notice-data/radix-LICENSE'):
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(repo / name, target)
        (self.source / 'uv.lock').write_text('version = 1\n')
        (self.source / 'web').mkdir()
        (self.source / 'web/package-lock.json').write_text('{"packages": {}}')
        sidecar = self.source / 'app/linux/tsnet-sidecar'
        (sidecar / 'third_party').mkdir(parents=True)
        (sidecar / 'go.sum').write_text('')
        self.original = (repo / 'packaging/fixtures/manifest-notices/google-uuid-LICENSE').read_bytes()
        vendor = self.source / 'vendor'
        vendor.mkdir()
        (vendor / 'LICENSE').write_bytes(self.original)
        self.vendor = dict(Path='example.com/vendor', Version='v1.0.0', Sum='h1:synthetic', Dir=str(vendor),
                           Replace=dict(Path='../vendor', Dir=str(vendor)))
        self.local = dict(Path='happyranch/tsnethealth', Version='v0.0.0', Dir=str(self.shared),
                          Replace=dict(Path='../../shared/tsnethealth', Dir=str(self.shared)))
        self.collector = runpy.run_path(str(BUILDER.with_name('collect-notices.py')))
        self.count = 0

    def collect(self, modules: list[dict]) -> Path:
        self.count += 1
        payload = self.work / f'payload-{self.count}'
        (payload / 'licenses').mkdir(parents=True)
        python = payload / 'python/lib/python3.12'
        python.mkdir(parents=True)
        (python / 'LICENSE.txt').write_text('synthetic interpreter notice\n')
        goroot = self.work / 'goroot'
        goroot.mkdir(exist_ok=True)
        (goroot / 'LICENSE').write_text('synthetic Go notice\n')
        raw = '\n'.join(json.dumps(dict(Module=module)) for module in modules)
        with patch.object(sys, 'argv', ['collect-notices.py', str(self.source), str(payload)]), \
                patch.dict(os.environ, GOMODCACHE=str(self.work / 'go-modules')), \
                patch('importlib.metadata.distributions', return_value=[]), \
                patch('subprocess.check_output', side_effect=[raw, str(goroot)]):
            self.collector['main']()
        return payload / 'licenses'

    def test_local_health_excluded_and_external_original_retained(self) -> None:
        for version in ('v0.0.0', None):
            with self.subTest(version=version):
                local = dict(self.local)
                if version is None:
                    local.pop('Version')
                notices = self.collect([dict(Path='happyranch/linux-tsnet-sidecar', Main=True),
                                        {}, local, self.vendor, self.vendor])
                self.assertEqual(json.loads((notices / 'go-modules.json').read_text()),
                                 [{key: self.vendor[key] for key in ('Path', 'Version', 'Sum')}])
                self.assertEqual((notices / 'go/example.com/vendor@v1.0.0/LICENSE').read_bytes(),
                                 self.original)
                self.assertFalse((notices / 'go/happyranch').exists())
                self.assertEqual((notices / 'HappyRanch-LICENSE').read_bytes(),
                                 (self.source / 'LICENSE').read_bytes())
                for retired in ('headscale', 'headscale-LICENSE', 'headscale-modules.json',
                                'headscale-build-info.json'):
                    self.assertFalse((notices / retired).exists(), retired)

    def test_unlicensed_near_miss_replacements_still_refused(self) -> None:
        for label, change in (
            ('third-party path', dict(Path='example.com/tsnethealth')),
            ('other first-party path', dict(Path='happyranch/other')),
            ('different replace', dict(Replace=dict(self.local['Replace'], Path='../tsnethealth'))),
            ('versioned replace', dict(Replace=dict(self.local['Replace'], Version='v1.0.0'))),
            ('outside source', dict(Dir=str(self.outside),
                                    Replace=dict(self.local['Replace'], Dir=str(self.outside)))),
            ('different replacement dir', dict(Replace=dict(self.local['Replace'], Dir=str(self.outside)))),
            ('no replacement', dict(Replace={})),
        ):
            with self.subTest(case=label):
                module = dict(self.local, **change)
                with self.assertRaisesRegex(RuntimeError, 'Go module original notice missing: ' + module['Path']):
                    self.collect([module])
        self.shared.rmdir()
        self.shared.symlink_to(self.outside, target_is_directory=True)
        with self.subTest(case='symlink escape'), self.assertRaisesRegex(
                RuntimeError, 'Go module original notice missing: happyranch/tsnethealth'):
            self.collect([self.local])


class DebPackageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='deb-test-', dir='/tmp')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)

    @staticmethod
    def elf(arch: str, stack: str = 'safe', kind: int = 2) -> bytes:
        # Minimal ELF64 program-header fixture, inspected but never executed.
        count = 1 + (kind == 2) + (stack != 'missing')
        size = 64 + count * 56 + 16
        header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0' * 9,
                             kind, 62 if arch == 'x86_64' else 183, 1, 0, 64, 0, 0,
                             64, 56, count, 64, 0, 0)
        program = (struct.pack('<IIQQQQQQ', 6, 4, 64, 0x400040, 0x400040,
                               count * 56, count * 56, 8) if kind == 2 else b'')
        program += struct.pack('<IIQQQQQQ', 1, 5, 0, 0x400000, 0x400000, size, size, 0x1000)
        if stack != 'missing':
            program += struct.pack('<IIQQQQQQ', 0x6474e551, 7 if stack == 'executable' else 6,
                                   0, 0, 0, 0, 0, 16)
        return header + program + b'payload contents'

    def payload(self, arch: str = 'x86_64', version: str = '0.1.0') -> Path:
        root = f'happyranch-{version}-linux-{arch}'
        payload = self.work / root
        files = {
            'build-info.json': json.dumps({'arch': arch, 'source_sha': '1' * 40,
                                          'dependencies': [['happyranch', version]]}),
            'web/dist/index.html': '<html>fixture</html>',
            'python/bin/python3.12': 'fixture interpreter',
            'python/lib/libpython3.12.so.1.0': 'fixture shared library',
            'python/lib/module.py': '# fixture source',
            'python/lib/__pycache__/module.cpython-312.pyc': 'generated cache',
            'share/happyranch/desktop/happyranch_window.py': '# fixture',
            'share/happyranch/desktop/host_bridge.py': '# inert fixture',
            'share/happyranch/desktop/connection_contract.py': '# inert fixture',
            'share/happyranch/desktop/native-connection-bridge.v1.json': '{}',
            'web/dist/resource-manifest.json': json.dumps({
                'version': 1, 'entry': 'index.html', 'resources': [{
                    'path': 'index.html', 'bytes': len(b'<html>fixture</html>'),
                    'sha256': hashlib.sha256(b'<html>fixture</html>').hexdigest()}]}),
            'licenses/HappyRanch-LICENSE': 'original license fixture',
            'licenses/nested/NOTICE': 'original dependency notice',
            'bin/happyranch-remote-access-helper': (BUILDER.parent / 'bin/happyranch-remote-access-helper').read_text(),
            'bin/happyranch-remote-access-policy-renew': (BUILDER.parent / 'bin/happyranch-remote-access-policy-renew').read_text(),
        }
        for name in ('remote_access_helper', 'helper_owner', 'helper_control', 'release_verifier',
                     'status_v2', 'home_audit', 'helper_generation', 'helper_lifecycle', 'helper_invite'):
            files['libexec/remote-access/' + name + '.py'] = (
                BUILDER.parents[2] / 'runtime/remote_access' / (name + '.py')).read_text()
        generator = runpy.run_path(str(BUILDER.with_name('retirement-preinst.py')))
        for name in generator['MODULES']:
            files['python/lib/python3.12/site-packages/runtime/remote_access/' + name + '.py'] = (
                BUILDER.parents[2] / 'runtime/remote_access' / (name + '.py')).read_text()
        for launcher in ('happyranch', 'happyranch-daemon', 'happyranch-app'):
            files['bin/' + launcher] = '#!/bin/sh\nexit 0\n'
        for size in (16, 32, 64, 128, 256, 512, 1024):
            files[f'share/icons/hicolor/{size}x{size}/apps/happyranch.png'] = 'icon fixture'
        files['share/icons/hicolor/scalable/apps/happyranch.svg'] = '<svg/>'
        files['share/applications/happyranch.desktop.in'] = (
            BUILDER.parent / 'desktop/happyranch.desktop.in').read_text()
        for name, text in files.items():
            path = payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            path.chmod(0o755 if name.startswith('bin/') or (name.startswith('libexec/') and not name.endswith('.py')) or name in (
                'python/lib/libpython3.12.so.1.0', 'licenses/nested/NOTICE') else 0o644)
        for name in ('python/bin/python3.12', 'python/lib/libpython3.12.so.1.0'):
            (payload / name).write_bytes(self.elf(arch))
        # Retained Go executables have no GNU_STACK; Python-only checking must
        # preserve their bytes on both payload architectures.
        for name in ('happyranch-tsnet-sidecar', 'happyranch-home-bridge'):
            binary = payload / 'bin' / name
            binary.write_bytes(self.elf(arch, 'missing'))
            binary.chmod(0o755)
        (payload / 'python/bin/python3').symlink_to('python3.12')
        archive = self.work / (root + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(payload, arcname=root)
        return archive

    def build(self, archive: Path) -> subprocess.CompletedProcess[str]:
        # A restrictive caller umask must not make the installed package private.
        return subprocess.run(['bash', BUILDER, archive, '--dist-dir', self.work / 'dist'],
                              text=True, capture_output=True, umask=0o077)

    def test_packaged_helper_directives(self) -> None:
        self.assertEqual((BUILDER.parent / 'dbus/ai.happyranch.RemoteAccess1.service').read_text().splitlines(), [
            '[D-BUS Service]', 'Name=ai.happyranch.RemoteAccess1',
            'Exec=/opt/happyranch-desktop/bin/happyranch-remote-access-helper',
            'User=root', 'SystemdService=happyranch-remote-access-helper.service',
        ])
        self.assertEqual((BUILDER.parent / 'systemd/happyranch-remote-access-helper.service').read_text().splitlines(), [
            '[Unit]', 'Description=HappyRanch remote access helper', '', '[Service]',
            'Type=dbus', 'BusName=ai.happyranch.RemoteAccess1', 'User=root',
            'ExecStart=/usr/bin/python3 -I /opt/happyranch-desktop/bin/happyranch-remote-access-helper',
            'NoNewPrivileges=yes', 'ProtectSystem=strict', 'ProtectHome=read-only', 'PrivateTmp=yes',
            'ReadWritePaths=/etc', 'ReadWritePaths=/var/lib/happyranch-remote-access',
            'ReadWritePaths=-/var/lib/happyranch-headscale',
            'ReadWritePaths=-/var/lib/happyranch-connector',
            'ReadWritePaths=-/var/lib/happyranch-tsnet-sidecar',
            'ReadWritePaths=-/run/happyranch-headscale',
            'ReadWritePaths=-/run/happyranch-connector',
            'ReadWritePaths=-/run/happyranch-tsnet-sidecar',
            'ReadWritePaths=-/opt/happyranch',
        ])

    @staticmethod
    def helper_unit_reference() -> bytes:
        # Packaging's stdlib-only venue reads the trusted source literal without
        # importing application dependencies or any scanned filesystem value.
        source = BUILDER.parents[2] / 'runtime/remote_access/helper_unit.py'
        assignment, = (node for node in ast.parse(source.read_bytes()).body
                       if isinstance(node, ast.Assign) and any(
                           isinstance(target, ast.Name) and target.id == 'PACKAGED_HELPER_UNIT_BYTES'
                           for target in node.targets))
        value = ast.literal_eval(assignment.value)
        assert isinstance(value, bytes)
        return value

    def test_helper_unit_reference_matches_shipped_bytes(self) -> None:
        self.assertEqual((BUILDER.parent / 'systemd/happyranch-remote-access-helper.service').read_bytes(),
                         self.helper_unit_reference())

    def test_packaged_user_unit_directives(self) -> None:
        self.assertEqual(UNIT.read_text().splitlines(), [
            '[Unit]',
            'Description=HappyRanch release daemon',
            'StartLimitIntervalSec=300',
            'StartLimitBurst=5',
            '',
            '[Service]',
            'Type=simple',
            'ExecStart=/usr/bin/python3 -I /opt/happyranch-desktop/share/happyranch/desktop/happyranch_window.py --prefix /opt/happyranch-desktop --run-service --user-home %h',
            'Environment=HAPPYRANCH_DAEMON_PORT=0',
            'Environment=HAPPYRANCH_DAEMON_BIND_HOST=127.0.0.1',
            'Environment=HAPPYRANCH_BUNDLED_BIN=/opt/happyranch-desktop/bin',
            'Environment=PATH=/opt/happyranch-desktop/bin:/usr/local/bin:/usr/bin:/bin',
            'EnvironmentFile=-%h/.config/happyranch/daemon.env',
            'Restart=on-failure',
            'RestartSec=5',
            'TimeoutStopSec=60',
            '',
            '[Install]',
            'WantedBy=default.target',
        ])

    def test_unsafe_elf_stacks_are_refused_before_output(self) -> None:
        for index, (name, stack) in enumerate(
            (name, stack) for name in ('python/bin/python3.12',
                                      'python/lib/libpython3.12.so.1.0',
                                      'python/lib/extensions/unusual-name')
            for stack in ('missing', 'executable')
        ):
            with self.subTest(elf=name, stack=stack):
                version = f'0.2.{index}'
                archive = self.payload(version=version)
                root = self.work / f'happyranch-{version}-linux-x86_64'
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(self.elf('x86_64', stack))
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(root, arcname=root.name)
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0, 'unsafe ELF was packaged')
                self.assertIn('unsafe GNU_STACK', result.stderr)
                self.assertIn(name.removeprefix('python/'), result.stderr)
                self.assertFalse(list((self.work / 'dist').glob('*.deb')))

    @staticmethod
    def relocatable_elf(arch: str) -> bytes:
        # ET_REL has sections for the linker, no runtime program-header table.
        header = struct.pack('<16sHHIQQQIHHHHHH', b'\x7fELF\x02\x01\x01' + b'\0' * 9,
                             1, 62 if arch == 'x86_64' else 183, 1, 0, 0, 64, 0,
                             64, 0, 0, 64, 10, 3)
        symbol = struct.pack('<IBBHQQ', 0, 0x10, 0, 2, 0, 0)
        # (type, flags, link, info, entry size, contents). Group info is a
        # symbol index, deliberately larger than the section count.
        entries = [
            (17, 0, 4, 11, 4, struct.pack('<II', 1, 2)),
            (1, 0x206, 0, 0, 0, b'build-only code\n'),
            (3, 0, 0, 0, 0, b'\0'),
            (2, 0, 3, 1, 24, b'\0' * 24 + symbol * 11),
            (4, 0x40, 4, 2, 24, struct.pack('<QQq', 0, 1 << 32, 0)),
            (9, 0x40, 4, 2, 16, struct.pack('<QQ', 0, 1 << 32)),
            (5, 0, 4, 0, 4, struct.pack('<IIII', 1, 1, 0, 0)),
            (6, 0, 3, 0, 16, b'\0' * 16),
            (18, 0, 4, 0, 4, b'\0' * 48),
        ]
        sections, contents = b'\0' * 64, b''
        for kind, flags, link, info, size, data in entries:
            sections += struct.pack('<IIQQQQIIQQ', 0, kind, flags, 0,
                                    704 + len(contents), len(data), link, info, 1, size)
            contents += data
        return header + sections + contents

    def test_relocatable_elf_is_preserved_in_payload(self) -> None:
        for arch in ('x86_64', 'aarch64'):
            with self.subTest(arch=arch):
                archive = self.payload(arch)
                root = self.work / f'happyranch-0.1.0-linux-{arch}'
                path = root / f'python/lib/python3.12/config-3.12-{arch}-linux-gnu/python.o'
                path.parent.mkdir(parents=True)
                original = self.relocatable_elf(arch)
                path.write_bytes(original)
                for fix in (False, True):
                    result = subprocess.run([sys.executable, '-B', '-I',
                                             str(BUILDER.with_name('python-elf-stack.py')),
                                             str(root / 'python'), *(['--fix'] if fix else [])],
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('NON-LOADABLE ET_REL:', result.stdout)
                    self.assertEqual(path.read_bytes(), original)
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(root, arcname=root.name)
                result = self.build(archive)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_unknown_and_malformed_elf_layouts_are_refused(self) -> None:
        for arch in ('x86_64', 'aarch64'):
            valid = self.relocatable_elf(arch)
            cases = {'truncated header': valid[:32], 'truncated sections': valid[:128]}
            for kind in (0, 4, 0xfe00, 0xffff):
                data = bytearray(self.elf(arch))  # safe GNU_STACK must not hide unknown e_type
                struct.pack_into('<H', data, 16, kind)
                cases[f'unknown type {kind}'] = data
            for name, offset, format_, value in (
                ('wrong class', 4, 'B', 1), ('wrong encoding', 5, 'B', 2),
                ('wrong ident version', 6, 'B', 0), ('wrong machine', 18, 'H', 40),
                ('wrong version', 20, 'I', 0), ('entry point', 24, 'Q', 1),
                ('program offset', 32, 'Q', 64), ('program count', 56, 'H', 1),
                ('header size', 52, 'H', 0), ('section offset', 40, 'Q', 1),
                ('section size', 58, 'H', 0), ('section count', 60, 'H', 0),
                ('section string index', 62, 'H', 10), ('section extent', 152, 'Q', 4096),
                ('section string type', 62, 'H', 2),
            ):
                data = bytearray(valid)
                struct.pack_into('<' + format_, data, offset, value)
                cases[name] = data
            for index, label in ((1, 'group'), (4, 'symtab'), (5, 'rela'), (6, 'rel'),
                                 (7, 'hash'), (8, 'dynamic'), (9, 'symtab_shndx')):
                for value in (0, 0xffffffff, 2):  # missing, out of range, wrong target type
                    data = bytearray(valid)
                    struct.pack_into('<I', data, 64 + index * 64 + 40, value)
                    cases[f'{label} link {value}'] = data
                data = bytearray(valid)
                struct.pack_into('<I', data, 64 + index * 64 + 44, 0xffffffff)
                cases[f'{label} info out of range'] = data
            data = bytearray(valid)
            struct.pack_into('<I', data, 64 + 4 * 64 + 4, 11)  # SHT_DYNSYM shares SYMTAB rules
            struct.pack_into('<I', data, 64 + 4 * 64 + 40, 0xffffffff)
            cases['dynsym link out of range'] = data
            for flags, field in ((0x40, 44), (0x80, 40)):
                data = bytearray(valid)
                struct.pack_into('<Q', data, 64 + 2 * 64 + 8, flags)
                struct.pack_into('<I', data, 64 + 2 * 64 + field, 0xffffffff)
                cases[f'flagged section reference {flags}'] = data
            root = self.work / arch
            root.mkdir()
            (root / 'safe-loadable').write_bytes(self.elf(arch))
            path = root / 'unusual-name'
            for name, data in cases.items():
                for fix in (False, True):
                    with self.subTest(arch=arch, case=name, fix=fix):
                        path.write_bytes(data)
                        result = subprocess.run([sys.executable, '-B', '-I',
                                                 str(BUILDER.with_name('python-elf-stack.py')),
                                                 str(root), *(['--fix'] if fix else [])],
                                                text=True, capture_output=True)
                        self.assertNotEqual(result.returncode, 0, 'invalid ELF was admitted')
                        self.assertIn('unsupported ELF layout:', result.stderr)
                        self.assertEqual(path.read_bytes(), data, 'invalid ELF was modified')
            with self.subTest(arch=arch, case='symtab link 4294967295', boundary='builder'):
                archive = self.payload(arch)
                payload = self.work / f'happyranch-0.1.0-linux-{arch}'
                object_path = payload / f'python/lib/python3.12/config-3.12-{arch}-linux-gnu/python.o'
                object_path.parent.mkdir(parents=True)
                data = cases['symtab link 4294967295']
                object_path.write_bytes(data)
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(payload, arcname=payload.name)
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0, 'invalid section link was packaged')
                self.assertIn('unsupported ELF layout:', result.stderr)
                self.assertEqual(object_path.read_bytes(), data, 'invalid ELF was modified')
                self.assertFalse(list((self.work / 'dist').glob('*.deb')))

    def test_elf_stack_repair_preserves_loads_and_payload(self) -> None:
        for arch in ('x86_64', 'aarch64'):
            for kind in (2, 3):  # executable with PT_PHDR; shared object without it
                for stack in ('missing', 'executable', 'safe'):
                    with self.subTest(arch=arch, kind=kind, stack=stack):
                        root = self.work / 'python'
                        root.mkdir(exist_ok=True)
                        path = root / 'elf'
                        before = self.elf(arch, stack, kind)
                        path.write_bytes(before)
                        command = [sys.executable, '-B', '-I',
                                   str(BUILDER.with_name('python-elf-stack.py')), str(root), '--fix']
                        result = subprocess.run(command, text=True, capture_output=True)
                        self.assertEqual(result.returncode, 0, result.stderr)
                        output = subprocess.check_output(['readelf', '-lW', path], text=True)
                        rows = [line.split() for line in output.splitlines()]
                        stacks = [row for row in rows if row and row[0] == 'GNU_STACK']
                        self.assertEqual(len(stacks), 1)
                        self.assertEqual(''.join(stacks[0][6:-1]), 'RW')
                        after = path.read_bytes()
                        if stack == 'missing':
                            self.assertEqual(after[64:len(before)], before[64:])
                            # The existing LOAD's address, permissions and extent
                            # survive; the additional LOAD maps only the table, R.
                            loads = [row for row in rows if row and row[0] == 'LOAD']
                            self.assertEqual(len(loads), 2)
                            self.assertEqual(int(loads[0][2], 16), 0x400000)
                            self.assertEqual(int(loads[0][4], 16), len(before))
                            self.assertEqual(''.join(loads[0][6:-1]), 'RE')
                            self.assertEqual(''.join(loads[1][6:-1]), 'R')
                        elif stack == 'executable':
                            self.assertEqual([i for i, (a, b) in enumerate(zip(before, after)) if a != b],
                                             [64 + (1 + (kind == 2)) * 56 + 4])
                            self.assertEqual(len(after), len(before))
                        else:
                            self.assertEqual(after, before)
                        repeated = subprocess.run(command, text=True, capture_output=True)
                        self.assertEqual(repeated.returncode, 0, repeated.stderr)
                        self.assertEqual(path.read_bytes(), after, 'repair must be byte-idempotent')

    def test_installed_contract_and_architecture(self) -> None:
        for payload_arch, debarch in (('x86_64', 'amd64'), ('aarch64', 'arm64')):
            with self.subTest(arch=payload_arch):
                result = self.build(self.payload(payload_arch))
                self.assertEqual(result.returncode, 0, result.stderr)
                deb = self.work / f'dist/happyranch_0.1.0-1_{debarch}.deb'
                self.assertTrue(deb.is_file())
                fields = subprocess.check_output(['dpkg-deb', '-f', deb], text=True)
                for field in ('Package: happyranch', 'Version: 0.1.0-1', f'Architecture: {debarch}',
                              'Maintainer:', 'Homepage:', 'Installed-Size:', 'Section:', 'Priority:',
                              'Description:', 'python3 (>= 3.10)', 'python3-gi', 'gir1.2-gtk-3.0',
                              'gir1.2-webkit2-4.1', 'git, ca-certificates', 'libc6 (>= 2.35)', 'libgcc-s1'):
                    self.assertIn(field, fields)
                self.assertEqual(subprocess.check_output(['dpkg-deb', '-f', deb, 'Depends'], text=True).strip(),
                                 'python3 (>= 3.10), python3-gi, gir1.2-gtk-3.0, gir1.2-webkit2-4.1 (>= 2.40), gir1.2-secret-1, '
                                 'git, ca-certificates, libc6 (>= 2.35), libgcc-s1')
                listing = subprocess.check_output(['dpkg-deb', '-c', deb], text=True)
                self.assertTrue(all(' root/root ' in line for line in listing.splitlines()))
                installed = self.work / ('installed-' + debarch)
                subprocess.run(['dpkg-deb', '-x', deb, installed], check=True)
                control = self.work / ('control-' + debarch)
                subprocess.run(['dpkg-deb', '-e', deb, control], check=True)
                self.assertEqual({p.name for p in control.iterdir()}, {'control', 'md5sums', 'preinst'})
                generator = runpy.run_path(str(BUILDER.with_name('retirement-preinst.py')))
                self.assertEqual((control / 'preinst').read_text(), generator['render_preinst'](BUILDER.parents[2]))
                self.assertEqual((control / 'preinst').stat().st_mode & 0o777, 0o755)
                subprocess.run(['md5sum', '--quiet', '-c', control / 'md5sums'], cwd=installed, check=True)
                self.assertFalse((installed / 'opt/happyranch').exists())
                self.assertFalse((installed / 'etc').exists())
                self.assertEqual((installed / 'usr/lib/systemd/system/happyranch-remote-access-helper.service').read_bytes(),
                                 self.helper_unit_reference())
                data = subprocess.check_output(['dpkg-deb', '--fsys-tarfile', deb])
                with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                    units = [member for member in archive.getmembers()
                             if member.name.removeprefix('./') ==
                             'usr/lib/systemd/user/happyranch-daemon.service']
                    self.assertEqual(len(units), 1, 'packaged user unit must exist at its exact path')
                    unit = units[0]
                    self.assertTrue(unit.isfile())
                    self.assertEqual((unit.uname, unit.gname, unit.uid, unit.gid), ('root', 'root', 0, 0))
                    self.assertEqual(unit.mode, 0o644)
                    self.assertEqual(archive.extractfile(unit).read(), UNIT.read_bytes())
                    for source, destination in (
                        ('dbus/ai.happyranch.RemoteAccess1.conf', 'usr/share/dbus-1/system.d/ai.happyranch.RemoteAccess1.conf'),
                        ('dbus/ai.happyranch.RemoteAccess1.service', 'usr/share/dbus-1/system-services/ai.happyranch.RemoteAccess1.service'),
                        ('polkit/ai.happyranch.remote-access.policy', 'usr/share/polkit-1/actions/ai.happyranch.remote-access.policy'),
                        ('systemd/happyranch-remote-access-helper.service', 'usr/lib/systemd/system/happyranch-remote-access-helper.service'),
                        ('bin/happyranch-remote-access-helper', 'opt/happyranch-desktop/bin/happyranch-remote-access-helper'),
                        ('bin/happyranch-remote-access-policy-renew', 'opt/happyranch-desktop/bin/happyranch-remote-access-policy-renew'),
                        ('systemd/happyranch-remote-access-policy-renew.service', 'usr/lib/systemd/system/happyranch-remote-access-policy-renew.service'),
                        ('systemd/happyranch-remote-access-policy-renew.timer', 'usr/lib/systemd/system/happyranch-remote-access-policy-renew.timer'),
                    ):
                        self.assertIn('./' + destination, archive.getnames())
                        member = archive.getmember('./' + destination)
                        self.assertTrue(member.isfile())
                        self.assertEqual((member.uid, member.gid, member.mode),
                                         (0, 0, 0o755 if source.startswith('bin/') else 0o644))
                        self.assertEqual(archive.extractfile(member).read(), (BUILDER.parent / source).read_bytes())
                    state = archive.getmember('./var/lib/happyranch-remote-access')
                    self.assertTrue(state.isdir())
                    self.assertEqual((state.uid, state.gid, state.mode), (0, 0, 0o700))
                    self.assertEqual(list((installed / 'var/lib/happyranch-remote-access').iterdir()), [])
                    self.assertFalse((installed / 'etc/systemd/system').exists(), 'no enablement links')
                    for name in ('remote_access_helper', 'helper_owner', 'helper_control', 'release_verifier',
                                 'status_v2', 'home_audit', 'helper_generation', 'helper_lifecycle', 'helper_invite'):
                        source = BUILDER.parents[2] / 'runtime/remote_access' / (name + '.py')
                        shipped = installed / 'opt/happyranch-desktop/libexec/remote-access' / (name + '.py')
                        self.assertEqual(shipped.read_bytes(), source.read_bytes())
                        self.assertEqual(shipped.stat().st_mode & 0o777, 0o644)
                self.assertEqual({p.name for p in (installed / 'opt').iterdir()}, {'happyranch-desktop'})
                for launcher in ('happyranch', 'happyranch-app'):
                    wrapper = installed / 'usr/bin' / launcher
                    self.assertEqual(wrapper.read_text(),
                                     f'#!/bin/sh\nexec /opt/happyranch-desktop/bin/{launcher} "$@"\n')
                    self.assertEqual(wrapper.stat().st_mode & 0o777, 0o755)
                desktop = (installed / 'usr/share/applications/happyranch.desktop').read_text()
                self.assertIn('Exec=/usr/bin/happyranch-app\n', desktop)
                self.assertIn('TryExec=/usr/bin/happyranch-app\n', desktop)
                self.assertIn('Icon=happyranch\n', desktop)
                self.assertNotIn('@APP_EXEC@', desktop)
                doc = installed / 'usr/share/doc/happyranch'
                self.assertIn('/usr/share/common-licenses/Apache-2.0', (doc / 'copyright').read_text())
                self.assertEqual((doc / 'licenses/HappyRanch-LICENSE').read_text(), 'original license fixture')
                self.assertEqual((installed / 'usr/share/doc/happyranch/licenses/nested/NOTICE').read_text(),
                                 'original dependency notice')
                self.assertTrue(all(path.stat().st_mode & 0o777 == 0o644
                                    for path in doc.rglob('*') if path.is_file() and not path.is_symlink()))
                self.assertIn('happyranch (0.1.0-1)', gzip.decompress((doc / 'changelog.Debian.gz').read_bytes()).decode())
                self.assertEqual(len(list((installed / 'usr/share/icons/hicolor').rglob('happyranch.*'))), 8)
                prefix = installed / 'opt/happyranch-desktop'
                for retired in ('headscale', 'happyranch-home-ca', 'happyranch-home-admin'):
                    self.assertFalse(list(prefix.rglob(retired)), retired)
                    self.assertFalse((installed / 'usr/bin' / retired).exists())
                for name in ('happyranch-tsnet-sidecar', 'happyranch-home-bridge'):
                    binary = prefix / 'bin' / name
                    self.assertEqual(binary.read_bytes(), self.elf(payload_arch, 'missing'))
                    self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
                    self.assertFalse(binary.is_symlink())
                self.assertTrue((prefix / 'bin/happyranch-remote-access-policy-renew').is_file())
                self.assertFalse(list(prefix.rglob('__pycache__')))
                self.assertTrue((prefix / 'python/lib/module.py').is_file())
                self.assertEqual((prefix / 'python/lib/libpython3.12.so.1.0').stat().st_mode & 0o777, 0o644)
                self.assertIn('Description: desktop and command-line workspace', fields)
                self.assertEqual(os.readlink(prefix / 'python/bin/python3'), 'python3.12')
                for path in installed.rglob('*'):
                    if path == installed / 'var/lib/happyranch-remote-access':
                        continue  # asserted exactly 0700 above
                    if not path.is_symlink():
                        self.assertEqual(path.stat().st_mode & 0o055, 0o055 if path.is_dir()
                                         or path.stat().st_mode & 0o111 else 0o044, str(path))
                before = deb.read_bytes()
                repeated = self.build(self.work / f'happyranch-0.1.0-linux-{payload_arch}.tar.gz')
                self.assertNotEqual(repeated.returncode, 0)
                self.assertIn('output already exists', repeated.stderr)
                self.assertEqual(deb.read_bytes(), before)

    def test_incomplete_retained_payload_is_refused(self) -> None:
        for index, missing in enumerate(('libexec/remote-access/status_v2.py',
                                         'libexec/remote-access/home_audit.py',
                                         'libexec/remote-access/helper_generation.py',
                                         'libexec/remote-access/helper_lifecycle.py',
                                         'libexec/remote-access/helper_invite.py',
                                         'bin/happyranch-remote-access-policy-renew',
                                         'share/happyranch/desktop/host_bridge.py')):
            with self.subTest(missing=missing):
                archive = self.payload(version=f'0.3.{index}')
                root = self.work / f'happyranch-0.3.{index}-linux-x86_64'
                (root / missing).unlink()
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(root, arcname=root.name)
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('incomplete desktop payload: ' + missing, result.stderr)
                self.assertFalse(list((self.work / 'dist').glob('*.deb')))

    def test_retired_controller_payload_is_refused(self) -> None:
        for index, retired in enumerate(('libexec/headscale/headscale',
                                        'libexec/remote-access/happyranch-home-ca',
                                        'libexec/remote-access/happyranch-home-admin')):
            with self.subTest(retired=retired):
                archive = self.payload(version=f'0.7.{index}')
                root = self.work / f'happyranch-0.7.{index}-linux-x86_64'
                binary = root / retired
                binary.parent.mkdir(parents=True, exist_ok=True)
                binary.write_bytes(self.elf('x86_64', 'missing'))
                binary.chmod(0o755)
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(root, arcname=root.name)
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('retired local controller payload: ' + retired, result.stderr)
                self.assertFalse(list((self.work / 'dist').glob('*.deb')))

    def test_retirement_payload_source_drift_is_refused(self) -> None:
        for index, case in enumerate(('missing', 'changed')):
            with self.subTest(case=case):
                archive = self.payload(version=f'0.6.{index}')
                root = self.work / f'happyranch-0.6.{index}-linux-x86_64'
                source = root / 'python/lib/python3.12/site-packages/runtime/remote_access/home_retirement.py'
                if case == 'missing':
                    source.unlink()
                else:
                    source.write_text('# synthetic mismatched installed recovery entry')
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(root, arcname=root.name)
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('retirement source differs from incoming control bundle', result.stderr)
                self.assertFalse(list((self.work / 'dist').glob('*.deb')))

    def test_tampered_main_manifest_is_refused(self) -> None:
        for index, case in enumerate(('missing', 'size', 'digest', 'extra', 'symlink', 'manifest')):
            with self.subTest(case=case):
                archive = self.payload(version=f'0.4.{index}')
                root = self.work / f'happyranch-0.4.{index}-linux-x86_64'
                connection = root / 'web/dist'
                entry = connection / 'index.html'
                if case == 'missing':
                    (connection / 'resource-manifest.json').unlink()
                elif case == 'size':
                    entry.write_text('truncated')
                elif case == 'digest':
                    entry.write_bytes(b'X' * entry.stat().st_size)
                elif case == 'extra':
                    (connection / 'unlisted.js').write_text('unlisted')
                elif case == 'symlink':
                    entry.rename(root / 'outside.html')
                    entry.symlink_to('../../outside.html')
                else:
                    (connection / 'resource-manifest.json').write_text('{}')
                with tarfile.open(archive, 'w:gz') as stream:
                    stream.add(root, arcname=root.name)
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('invalid main resource manifest or assets', result.stderr)
                self.assertFalse(list((self.work / 'dist').glob('*.deb')))

    def test_version_mapping(self) -> None:
        for version, debversion in (('1.2.3rc1', '1.2.3~rc1-1'), ('1.2.3.dev2', '1.2.3~dev2-1'),
                                    ('1.2.3.post1', '1.2.3+post1-1')):
            with self.subTest(version=version):
                result = self.build(self.payload(version=version))
                self.assertEqual(result.returncode, 0, result.stderr)
                deb = self.work / f'dist/happyranch_{debversion}_amd64.deb'
                self.assertTrue(deb.is_file(), f'expected mapped package: {deb.name}')
                self.assertEqual(subprocess.check_output(['dpkg-deb', '-f', deb, 'Version'], text=True).strip(),
                                 debversion)

    def test_debian_revision(self) -> None:
        archive = self.payload()
        for revision in ('1', '2'):
            result = subprocess.run(['bash', BUILDER, archive, '--dist-dir', self.work / 'dist',
                                     '--debian-revision', revision], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            deb = self.work / f'dist/happyranch_0.1.0-{revision}_amd64.deb'
            self.assertEqual(subprocess.check_output(['dpkg-deb', '-f', deb, 'Version'], text=True).strip(),
                             '0.1.0-' + revision)
        subprocess.run(['dpkg', '--compare-versions', '0.1.0-2', 'gt', '0.1.0-1'], check=True)
        for invalid in ('0', '-1', '01', '2/../3', '2\nVersion: 9', '2.1'):
            with self.subTest(invalid=invalid):
                result = subprocess.run(['bash', BUILDER, archive, '--debian-revision=' + invalid],
                                        text=True, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('must be a positive integer', result.stderr)

    def test_unsafe_archive_is_refused_before_output(self) -> None:
        for index, kind in enumerate(('traversal', 'escaping-link', 'link-parent')):
            with self.subTest(kind=kind):
                version = f'0.1.{index}'
                archive = self.payload(version=version)
                root = f'happyranch-{version}-linux-x86_64'
                with tarfile.open(archive, 'w:gz') as stream:
                    # Keep an otherwise complete payload so a missing guard cannot
                    # pass this negative through an unrelated metadata refusal.
                    stream.add(self.work / root, arcname=root)
                    member = tarfile.TarInfo(root + ('/../escape' if kind == 'traversal' else '/link'))
                    if kind != 'traversal':
                        member.type = tarfile.SYMTYPE
                        member.linkname = '../../escape' if kind == 'escaping-link' else 'target'
                    stream.addfile(member, io.BytesIO(b''))
                    if kind == 'link-parent':
                        stream.addfile(tarfile.TarInfo(root + '/link/file'), io.BytesIO(b''))
                result = self.build(archive)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn({'traversal': 'unsafe or duplicate', 'escaping-link': 'symlink escapes',
                               'link-parent': 'non-directory ancestor'}[kind], result.stderr)
                self.assertFalse((self.work / 'escape').exists())
                self.assertFalse((self.work / 'dist').exists())


if __name__ == '__main__':
    unittest.main()
