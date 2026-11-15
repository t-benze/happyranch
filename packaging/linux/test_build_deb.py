"""Focused native packaging checks through the real dpkg-deb boundary."""
from __future__ import annotations

import gzip
import io
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

BUILDER = Path(__file__).with_name('build-deb.sh')
UNIT = BUILDER.parent / 'systemd/happyranch-daemon.service'


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
            'licenses/HappyRanch-LICENSE': 'original license fixture',
            'licenses/nested/NOTICE': 'original dependency notice',
        }
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
            path.chmod(0o755 if name.startswith('bin/') or name in (
                'python/lib/libpython3.12.so.1.0', 'licenses/nested/NOTICE') else 0o644)
        (payload / 'python/bin/python3').symlink_to('python3.12')
        archive = self.work / (root + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(payload, arcname=root)
        return archive

    def build(self, archive: Path) -> subprocess.CompletedProcess[str]:
        # A restrictive caller umask must not make the installed package private.
        return subprocess.run(['bash', BUILDER, archive, '--dist-dir', self.work / 'dist'],
                              text=True, capture_output=True, umask=0o077)

    def test_packaged_user_unit_directives(self) -> None:
        self.assertEqual(UNIT.read_text().splitlines(), [
            '[Unit]',
            'Description=HappyRanch release daemon',
            'StartLimitIntervalSec=300',
            'StartLimitBurst=5',
            '',
            '[Service]',
            'Type=simple',
            'ExecStart=/opt/happyranch-desktop/bin/happyranch-daemon',
            'Environment=HAPPYRANCH_DAEMON_HOME=%h/.happyranch-release',
            'Environment=HAPPYRANCH_DAEMON_PORT=8766',
            'Environment=HAPPYRANCH_DAEMON_BIND_HOST=127.0.0.1',
            'Environment=HAPPYRANCH_BUNDLED_BIN=/opt/happyranch-desktop/bin',
            'Environment=PATH=/opt/happyranch-desktop/bin:/usr/local/bin:/usr/bin:/bin',
            'EnvironmentFile=-%h/.config/happyranch/daemon.env',
            'Restart=on-failure',
            'RestartSec=5',
            '',
            '[Install]',
            'WantedBy=default.target',
        ])

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
                self.assertFalse(list(prefix.rglob('__pycache__')))
                self.assertTrue((prefix / 'python/lib/module.py').is_file())
                self.assertEqual((prefix / 'python/lib/libpython3.12.so.1.0').stat().st_mode & 0o777, 0o644)
                self.assertIn('Description: desktop and command-line workspace', fields)
                self.assertEqual(os.readlink(prefix / 'python/bin/python3'), 'python3.12')
                for path in installed.rglob('*'):
                    if not path.is_symlink():
                        self.assertEqual(path.stat().st_mode & 0o055, 0o055 if path.is_dir()
                                         or path.stat().st_mode & 0o111 else 0o044, str(path))
                before = deb.read_bytes()
                repeated = self.build(self.work / f'happyranch-0.1.0-linux-{payload_arch}.tar.gz')
                self.assertNotEqual(repeated.returncode, 0)
                self.assertIn('output already exists', repeated.stderr)
                self.assertEqual(deb.read_bytes(), before)

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
