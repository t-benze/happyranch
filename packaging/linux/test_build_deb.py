"""Focused native packaging checks through the real dpkg-deb boundary."""
from __future__ import annotations

import ast
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
import struct
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

BUILDER = Path(__file__).with_name('build-deb.sh')
UNIT = BUILDER.parent / 'systemd/happyranch-daemon.service'


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


class HeadscalePayloadTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory(prefix='headscale-payload-test-', dir='/tmp')
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.payload = self.work / 'payload'
        self.payload.mkdir()
        self.collector = runpy.run_path(str(BUILDER.with_name('collect-notices.py')))
        self.globals = self.collector['install_headscale'].__globals__
        self.data = b'synthetic inert Headscale binary; never execute\n'
        self.pin = json.loads(BUILDER.with_name('headscale-pin.json').read_text())
        self.pin['assets']['amd64']['sha256'] = hashlib.sha256(self.data).hexdigest()
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, GOMODCACHE=str(self.work / 'go-modules')).start()
        patch.dict(self.globals, headscale_pin=lambda: self.pin).start()
        self.opener = patch('urllib.request.build_opener').start().return_value

    def install(self, data: bytes) -> None:
        self.opener.open.return_value = io.BytesIO(data)
        self.collector['install_headscale']('amd64', self.work, self.payload)

    def notice_fixture(self, *, licensed: bool = True) -> list[str]:
        binary = self.payload / 'libexec/headscale/headscale'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(self.data)
        (self.payload / 'licenses').mkdir()
        module = self.work / 'module'
        module.mkdir()
        if licensed:
            (module / 'LICENSE').write_bytes(b'original dependency notice\n')
        goroot = self.work / 'goroot'
        goroot.mkdir()
        (goroot / 'LICENSE').write_bytes(b'original Go 1.23.4 notice\n')
        vendor = goroot / 'src/vendor/example'
        vendor.mkdir(parents=True)
        (vendor / 'NOTICE').write_bytes(b'original Go vendor notice\n')
        raw = (f'{binary}: go1.23.4\n\tmod\tgithub.com/juanfont/headscale\t(devel)\n'
               '\tdep\texample.com/linked\tv1.2.3\th1:synthetic\n')
        return [raw, json.dumps(dict(Path='example.com/linked', Version='v1.2.3',
                                    Sum='h1:synthetic', Dir=str(module))),
                json.dumps(dict(Path='golang.org/toolchain',
                                Version='v0.0.1-go1.23.4.linux-amd64',
                                Sum='h1:toolchain', Dir=str(goroot)))]

    def test_headscale_pin_mismatch_and_download_failure_leave_no_install(self) -> None:
        with self.assertRaisesRegex(RuntimeError, '^LINUX_HEADSCALE_SHA256_MISMATCH$'):
            self.install(b'tampered binary')
        self.assertFalse((self.payload / 'libexec/headscale/headscale').exists())
        self.opener.open.side_effect = OSError('synthetic transport failure')
        with self.assertRaisesRegex(RuntimeError, '^LINUX_HEADSCALE_DOWNLOAD_FAILED$'):
            self.install(self.data)
        self.assertFalse((self.payload / 'libexec/headscale/headscale').exists())

    def test_headscale_verified_install_is_inert_and_executable(self) -> None:
        for arch in ('amd64', 'arm64'):
            with self.subTest(arch=arch):
                data = self.data + arch.encode()
                self.pin['assets'][arch]['sha256'] = hashlib.sha256(data).hexdigest()
                self.opener.open.return_value = io.BytesIO(data)
                self.collector['install_headscale'](arch, self.work, self.payload)
                binary = self.payload / 'libexec/headscale/headscale'
                self.assertTrue(binary.is_file())
                self.assertFalse(binary.is_symlink())
                self.assertEqual(binary.read_bytes(), data)
                self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
                self.assertFalse((self.payload / 'bin').exists())
                self.assertEqual(self.opener.open.call_args.args,
                                 ('https://github.com/juanfont/headscale/releases/download/v0.25.1/'
                                  'headscale_0.25.1_linux_' + arch,))
                self.assertEqual(self.opener.open.call_args.kwargs, {'timeout': 20})

    def test_headscale_redirect_hosts_and_stream_deadline(self) -> None:
        redirect = self.collector['HeadscaleRedirect']()
        with patch('urllib.request.HTTPRedirectHandler.redirect_request', return_value='accepted') as base:
            for url in ('http://github.com/asset', 'https://untrusted.example/asset',
                        'https://github.com@untrusted.example/asset',
                        'https://release-assets.githubusercontent.com:444/asset'):
                with self.subTest(url=url), self.assertRaisesRegex(RuntimeError, 'redirect refused'):
                    redirect.redirect_request(None, None, 302, '', {}, url)
            base.assert_not_called()
            for host in ('github.com', 'release-assets.githubusercontent.com'):
                self.assertEqual(redirect.redirect_request(None, None, 302, '', {},
                                                          f'https://{host}/asset'), 'accepted')
        for case, clock in (('nonempty_after_deadline', [0, 181]),
                            ('terminal_eof_after_deadline', [0, 179, 181])):
            with self.subTest(case=case):
                work = self.work / case
                work.mkdir()
                self.opener.open.return_value = io.BytesIO(self.data)
                # BytesIO returns the complete data, then EOF. The second case
                # expires only after that terminal read, with a valid checksum.
                with patch('time.monotonic', side_effect=clock), self.assertRaisesRegex(
                        RuntimeError, '^LINUX_HEADSCALE_DOWNLOAD_FAILED$'):
                    self.collector['install_headscale']('amd64', work, self.payload)
                self.assertFalse((self.payload / 'libexec/headscale/headscale').exists())

    def test_headscale_linked_notices_and_exact_toolchain(self) -> None:
        with patch('subprocess.check_output', side_effect=self.notice_fixture()) as go:
            self.collector['collect_headscale'](BUILDER.parents[2], self.payload)
        licenses = self.payload / 'licenses'
        for name in ('headscale-LICENSE', 'headscale-modules.json', 'headscale-build-info.json',
                     'headscale/modules/example.com/linked@v1.2.3/LICENSE', 'headscale/go-runtime/LICENSE'):
            self.assertTrue((licenses / name).is_file(), name)
        self.assertEqual((licenses / 'headscale-LICENSE').read_bytes(),
                         BUILDER.with_name('headscale-LICENSE').read_bytes())
        self.assertEqual(json.loads((licenses / 'headscale-modules.json').read_text()),
                         [dict(Path='example.com/linked', Version='v1.2.3', Sum='h1:synthetic')])
        self.assertEqual((licenses / 'headscale/modules/example.com/linked@v1.2.3/LICENSE').read_bytes(),
                         b'original dependency notice\n')
        self.assertEqual((licenses / 'headscale/go-runtime/LICENSE').read_bytes(),
                         b'original Go 1.23.4 notice\n')
        self.assertEqual((licenses / 'headscale/go-runtime/src/vendor/example/NOTICE').read_bytes(),
                         b'original Go vendor notice\n')
        info = json.loads((licenses / 'headscale-build-info.json').read_text())
        self.assertEqual(info['GoVersion'], 'go1.23.4')
        self.assertEqual(info['Tag'], 'v0.25.1')
        for call in go.call_args_list[1:]:
            self.assertEqual(call.args[0][:4], ['go', 'mod', 'download', '-json'])
            self.assertEqual(call.kwargs['env']['GOSUMDB'], 'sum.golang.org')
            self.assertEqual(call.kwargs['env']['GONOSUMDB'], '')
            self.assertEqual(call.kwargs['env']['GOINSECURE'], '')

    def test_headscale_missing_original_and_wrong_module_sum_refused(self) -> None:
        results = self.notice_fixture(licensed=False)
        with patch('subprocess.check_output', side_effect=results), self.assertRaisesRegex(
                RuntimeError, '^LINUX_HEADSCALE_NOTICE_MISSING: example.com/linked$'):
            self.collector['collect_headscale'](BUILDER.parents[2], self.payload)
        changed = json.loads(results[1])
        changed['Sum'] = 'h1:wrong'
        with patch('subprocess.check_output', side_effect=[results[0], json.dumps(changed)]), \
                self.assertRaisesRegex(RuntimeError, '^LINUX_HEADSCALE_MODULE_VERIFY_FAILED: example.com/linked$'):
            self.collector['collect_headscale'](BUILDER.parents[2], self.payload)
        self.assertFalse((self.payload / 'licenses/headscale-modules.json').exists())


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
                     'packaging/linux/headscale-LICENSE',
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
        binary = payload / 'libexec/headscale/headscale'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'synthetic Headscale for complete collector')
        pin = json.loads(BUILDER.with_name('headscale-pin.json').read_text())
        pin['assets']['amd64']['sha256'] = hashlib.sha256(binary.read_bytes()).hexdigest()
        headscale = (f'{binary}: go1.23.4\n\tdep\texample.com/vendor\tv1.0.0\th1:synthetic\n')
        original = json.dumps({key: self.vendor[key] for key in ('Path', 'Version', 'Sum', 'Dir')})
        toolchain = json.dumps(dict(Path='golang.org/toolchain', Version='v0.0.1-go1.23.4.linux-amd64',
                                    Sum='h1:toolchain', Dir=str(goroot)))
        with patch.object(sys, 'argv', ['collect-notices.py', str(self.source), str(payload)]), \
                patch.dict(os.environ, GOMODCACHE=str(self.work / 'go-modules')), \
                patch('importlib.metadata.distributions', return_value=[]), \
                patch.dict(self.collector['main'].__globals__, headscale_pin=lambda: pin), \
                patch('subprocess.check_output', side_effect=[raw, str(goroot), headscale, original, toolchain]):
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
                self.assertTrue((notices / 'headscale-modules.json').is_file())
                self.assertEqual(json.loads((notices / 'headscale-modules.json').read_text()),
                                 [{key: self.vendor[key] for key in ('Path', 'Version', 'Sum')}])
                self.assertEqual((notices / 'headscale/modules/example.com/vendor@v1.0.0/LICENSE').read_bytes(),
                                 self.original)

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
            'licenses/headscale-LICENSE': BUILDER.with_name('headscale-LICENSE').read_text(),
            'licenses/headscale-modules.json': '[{"Path":"example.com/linked","Version":"v1.2.3","Sum":"h1:synthetic"}]',
            'licenses/headscale-build-info.json': '{"Tag":"v0.25.1","GoVersion":"go1.23.4"}',
            'licenses/headscale/modules/example.com/linked@v1.2.3/LICENSE': 'original Headscale dependency notice',
            'licenses/headscale/go-runtime/LICENSE': 'original Go 1.23.4 notice',
            'libexec/headscale/headscale': 'fixture Headscale',
            'libexec/remote-access/happyranch-home-ca': 'fixture home CA materializer',
            'libexec/remote-access/happyranch-home-admin': 'fixture home admin runner',
            'bin/happyranch-remote-access-helper': (BUILDER.parent / 'bin/happyranch-remote-access-helper').read_text(),
            'bin/happyranch-remote-access-policy-renew': (BUILDER.parent / 'bin/happyranch-remote-access-policy-renew').read_text(),
        }
        for name in ('remote_access_helper', 'helper_owner', 'helper_control', 'release_verifier',
                     'status_v2', 'home_audit', 'helper_generation', 'helper_lifecycle', 'helper_invite'):
            files['libexec/remote-access/' + name + '.py'] = (
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
        # Static Go binary has no GNU_STACK: Python-only ELF checks must leave
        # its verified upstream bytes unchanged, on both architectures.
        (payload / 'libexec/headscale/headscale').write_bytes(self.elf(arch, 'missing'))
        for name in ('happyranch-home-ca', 'happyranch-home-admin'):
            (payload / 'libexec/remote-access' / name).write_bytes(self.elf(arch, 'missing'))
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
        source = BUILDER.parents[2] / 'runtime/remote_access/installer.py'
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
                self.assertEqual({p.name for p in control.iterdir()}, {'control', 'md5sums'})
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
                    materializers = [m for m in archive.getmembers() if m.name.removeprefix('./') ==
                                     'opt/happyranch-desktop/libexec/remote-access/happyranch-home-ca']
                    self.assertEqual(len(materializers), 1)
                    materializer = materializers[0]
                    self.assertTrue(materializer.isfile())
                    self.assertEqual((materializer.uid, materializer.gid, materializer.mode), (0, 0, 0o755))
                    self.assertEqual(archive.extractfile(materializer).read(), self.elf(payload_arch, 'missing'))
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
                binary = prefix / 'libexec/headscale/headscale'
                self.assertEqual(binary.read_bytes(), self.elf(payload_arch, 'missing'))
                self.assertEqual(binary.stat().st_mode & 0o777, 0o755)
                self.assertFalse(binary.is_symlink())
                self.assertFalse((prefix / 'bin/headscale').exists())
                self.assertFalse((installed / 'usr/bin/headscale').exists())
                self.assertFalse((prefix / 'bin/happyranch-home-ca').exists())
                self.assertFalse((installed / 'usr/bin/happyranch-home-ca').exists())
                admin = prefix / 'libexec/remote-access/happyranch-home-admin'
                self.assertEqual(admin.read_bytes(), self.elf(payload_arch, 'missing'))
                self.assertEqual(admin.stat().st_mode & 0o777, 0o755)
                self.assertFalse(admin.is_symlink())
                self.assertFalse((prefix / 'bin/happyranch-home-admin').exists())
                self.assertFalse((installed / 'usr/bin/happyranch-home-admin').exists())
                materializer = prefix / 'libexec/remote-access/happyranch-home-ca'
                self.assertEqual(materializer.stat().st_mode & 0o777, 0o755)
                self.assertFalse(materializer.is_symlink())
                # No aliases anywhere on either shipped PATH surface.
                for directory in (prefix / 'bin', installed / 'usr/bin'):
                    self.assertTrue(all('happyranch-home-ca' not in p.read_text() for p in directory.iterdir()))
                for name in ('headscale-LICENSE', 'headscale-modules.json', 'headscale-build-info.json',
                             'headscale/modules/example.com/linked@v1.2.3/LICENSE', 'headscale/go-runtime/LICENSE'):
                    expected = self.work / f'happyranch-0.1.0-linux-{payload_arch}/licenses' / name
                    self.assertEqual((prefix / 'licenses' / name).read_bytes(), expected.read_bytes())
                    self.assertEqual((doc / 'licenses' / name).read_bytes(), expected.read_bytes())
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

    def test_incomplete_headscale_payload_is_refused(self) -> None:
        for index, missing in enumerate(('libexec/headscale/headscale', 'licenses/headscale-LICENSE',
                                         'licenses/headscale-modules.json', 'licenses/headscale-build-info.json',
                                         'licenses/headscale/go-runtime/LICENSE',
                                         'libexec/remote-access/happyranch-home-ca',
                                         'libexec/remote-access/happyranch-home-admin',
                                         'libexec/remote-access/status_v2.py',
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
