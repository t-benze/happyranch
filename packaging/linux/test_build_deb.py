"""Focused native packaging checks through the real dpkg-deb boundary."""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

BUILDER = Path(__file__).with_name('build-deb.sh')


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
            path.chmod(0o755 if name.startswith('bin/') else 0o644)
        (payload / 'python/bin/python3').symlink_to('python3.12')
        archive = self.work / (root + '.tar.gz')
        with tarfile.open(archive, 'w:gz') as stream:
            stream.add(payload, arcname=root)
        return archive

    def build(self, archive: Path) -> subprocess.CompletedProcess[str]:
        # A restrictive caller umask must not make the installed package private.
        return subprocess.run(['bash', BUILDER, archive, '--dist-dir', self.work / 'dist'],
                              text=True, capture_output=True, umask=0o077)

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
                self.assertFalse((installed / 'usr/lib/systemd').exists())
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
                self.assertEqual((installed / 'usr/share/doc/happyranch/copyright').read_text(),
                                 'original license fixture')
                self.assertEqual((installed / 'usr/share/doc/happyranch/licenses/nested/NOTICE').read_text(),
                                 'original dependency notice')
                self.assertEqual(len(list((installed / 'usr/share/icons/hicolor').rglob('happyranch.*'))), 8)
                prefix = installed / 'opt/happyranch-desktop'
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
