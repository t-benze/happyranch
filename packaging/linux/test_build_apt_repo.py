"""Native CLI contract tests: real dpkg indexer, gpg signing and gpgv verification."""
from __future__ import annotations

import gzip
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

BUILDER = Path(__file__).with_name('build-apt-repo.py')


class AptRepoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Keep GPG's agent socket below Linux's AF_UNIX pathname limit even
        # when the task runner supplies a long TMPDIR.
        cls.keytemp = tempfile.TemporaryDirectory(prefix='apt-test-key-', dir='/tmp')
        cls.gnupg = Path(cls.keytemp.name)
        cls.gnupg.chmod(0o700)
        cls.env = dict(os.environ, GNUPGHOME=str(cls.gnupg))
        subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                        '--quick-generate-key', 'Throwaway apt test <apt-test@example.invalid>',
                        'ed25519', 'sign', '0'], env=cls.env, check=True, capture_output=True)
        cls.public = subprocess.check_output(['gpg', '--batch', '--export'], env=cls.env)
        cls.armored = subprocess.check_output(['gpg', '--batch', '--armor', '--export'], env=cls.env)

    @classmethod
    def tearDownClass(cls) -> None:
        subprocess.run(['gpgconf', '--kill', 'gpg-agent'], env=cls.env, check=True)
        cls.keytemp.cleanup()

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix='apt-test-')
        self.addCleanup(self.temporary.cleanup)
        self.work = Path(self.temporary.name)
        self.repo = self.work / 'repo'
        self.key = self.work / 'public.gpg'
        self.key.write_bytes(self.public)
        self.inrelease = self.work / 'InRelease'
        self.detached = self.work / 'Release.gpg'

    def cli(self, *args: str | Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(['/usr/bin/python3', '-B', BUILDER, *args], text=True, capture_output=True)

    def deb(self, version: str = '1.0-1', arch: str = 'amd64', content: str = 'fixture') -> Path:
        root = self.work / f'package-{version}-{arch}-{content}'
        (root / 'DEBIAN').mkdir(parents=True)
        (root / 'DEBIAN/control').write_text(
            f'Package: happyranch\nVersion: {version}\nArchitecture: {arch}\n'
            'Maintainer: Test <test@example.invalid>\nDescription: synthetic test package\n')
        (root / 'fixture').write_text(content)
        deb = root.parent / (root.name + '.deb')
        subprocess.run(['dpkg-deb', '--root-owner-group', '--build', root, deb],
                       check=True, capture_output=True)
        return deb

    def prepare(self, *debs: Path) -> subprocess.CompletedProcess[str]:
        result = self.cli('prepare', '--repo-dir', self.repo, *debs)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def sign(self) -> None:
        # Only this test fixture generates/uses a temporary secret key. Production
        # is invoked as a separate process and receives the PUBLIC export only.
        release = self.repo / 'dists/stable/Release'
        for output, option in ((self.inrelease, '--clearsign'), (self.detached, '-abs')):
            subprocess.run(['gpg', '--batch', '--yes', '--output', output, option, release],
                           env=self.env, check=True, capture_output=True)

    def finalize(self) -> subprocess.CompletedProcess[str]:
        return self.cli('finalize', '--repo-dir', self.repo, '--inrelease', self.inrelease,
                        '--release-gpg', self.detached, '--public-key', self.key)

    def test_layout_idempotence_and_incremental_versions(self) -> None:
        amd64, arm64 = self.deb(), self.deb(arch='arm64')
        result = self.prepare(amd64, arm64)
        self.assertIn('--clearsign', result.stdout)
        self.assertIn('-abs', result.stdout)
        suite = self.repo / 'dists/stable'
        release = (suite / 'Release').read_text()
        for field in ('Origin: HappyRanch', 'Label: HappyRanch', 'Suite: stable', 'Codename: stable',
                      'Architectures: amd64 arm64', 'Components: main', 'Date:', 'MD5Sum:', 'SHA256:'):
            self.assertIn(field, release)
        self.assertNotIn('Valid-Until:', release)
        for arch, deb in (('amd64', amd64), ('arm64', arm64)):
            path = suite / f'main/binary-{arch}/Packages'
            packages = path.read_bytes()
            self.assertEqual(gzip.decompress(path.with_suffix('.gz').read_bytes()), packages)
            self.assertIn(f'Architecture: {arch}\n', packages.decode())
            self.assertNotIn('Architecture: ' + ('arm64' if arch == 'amd64' else 'amd64'), packages.decode())
            self.assertIn(f'Filename: pool/main/h/happyranch/happyranch_1.0-1_{arch}.deb', packages.decode())
            self.assertIn('SHA256: ' + hashlib.sha256(deb.read_bytes()).hexdigest(), packages.decode())
            self.assertIn('Size: ' + str(deb.stat().st_size), packages.decode())
            for index in (path, path.with_suffix('.gz')):
                for algorithm in ('md5', 'sha256'):
                    self.assertIn(hashlib.new(algorithm, index.read_bytes()).hexdigest(), release)
        self.assertTrue((self.repo / '.nojekyll').is_file())
        self.assertIn('Signed-By:', (self.repo / 'README.txt').read_text())
        self.assertTrue((self.repo / 'index.html').is_file())
        before = {p.relative_to(self.repo): p.read_bytes() for p in self.repo.rglob('*') if p.is_file()}
        self.prepare(amd64, arm64)
        self.assertEqual(before, {p.relative_to(self.repo): p.read_bytes()
                                 for p in self.repo.rglob('*') if p.is_file()})
        self.sign()
        self.assertEqual(self.finalize().returncode, 0)
        self.prepare(self.deb('1.0-2'))
        self.assertFalse((suite / 'InRelease').exists())
        self.assertFalse((suite / 'Release.gpg').exists())
        packages = (suite / 'main/binary-amd64/Packages').read_text()
        self.assertIn('Version: 1.0-1\n', packages)
        self.assertIn('Version: 1.0-2\n', packages)
        self.assertEqual(len(list((self.repo / 'pool/main/h/happyranch').glob('*.deb'))), 3)
        # Same identity may be replayed only with the same package bytes.
        conflict = self.cli('prepare', '--repo-dir', self.repo, self.deb(content='changed'))
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn('different bytes', conflict.stderr)
        self.assertEqual((self.repo / 'pool/main/h/happyranch/happyranch_1.0-1_amd64.deb').read_bytes(),
                         amd64.read_bytes())

    def test_finalize_publishes_verified_public_tree(self) -> None:
        self.prepare(self.deb())
        self.sign()
        # Both binary and public-armored exports produce the dearmored public file.
        for certificate in (self.public, self.armored):
            self.key.write_bytes(certificate)
            result = self.finalize()
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual((self.repo / 'happyranch-archive-keyring.gpg').read_bytes(), self.public)
            self.assertEqual((self.repo / 'dists/stable/InRelease').read_bytes(), self.inrelease.read_bytes())
            self.assertEqual((self.repo / 'dists/stable/Release.gpg').read_bytes(), self.detached.read_bytes())
            self.assertTrue(all(p.stat().st_mode & 0o044 == 0o044 for p in self.repo.rglob('*') if p.is_file()))

    def test_finalize_fails_closed(self) -> None:
        deb = self.deb()
        for case in ('missing-inline', 'missing-detached', 'bad-inline', 'bad-detached',
                     'changed-release', 'changed-inline-release', 'changed-index', 'changed-pool', 'wrong-key'):
            with self.subTest(case=case):
                self.prepare(deb)
                self.sign()
                self.key.write_bytes(self.public)
                if case == 'missing-inline':
                    self.inrelease.unlink()
                elif case == 'missing-detached':
                    self.detached.unlink()
                elif case == 'bad-inline':
                    self.inrelease.write_bytes(b'not a signature')
                elif case == 'bad-detached':
                    self.detached.write_bytes(b'not a signature')
                elif case in ('changed-release', 'changed-inline-release'):
                    release = self.repo / 'dists/stable/Release'
                    release.write_bytes(release.read_bytes().replace(b'Label: HappyRanch', b'Label: Altered'))
                    if case == 'changed-inline-release':
                        # Detached is current/valid, but inline contains the old Release.
                        subprocess.run(['gpg', '--batch', '--yes', '--output', self.detached, '-abs', release],
                                       env=self.env, check=True, capture_output=True)
                elif case == 'changed-index':
                    (self.repo / 'dists/stable/main/binary-amd64/Packages').write_text('altered\n')
                elif case == 'changed-pool':
                    pooldeb = self.repo / 'pool/main/h/happyranch/happyranch_1.0-1_amd64.deb'
                    original = pooldeb.read_bytes()
                    pooldeb.write_bytes(self.deb(content='different').read_bytes())
                else:
                    # A public-only certificate from a different throwaway signer.
                    with tempfile.TemporaryDirectory(prefix='apt-wrong-key-', dir='/tmp') as temp:
                        env = dict(os.environ, GNUPGHOME=temp)
                        subprocess.run(['gpg', '--batch', '--pinentry-mode', 'loopback', '--passphrase', '',
                                        '--quick-generate-key', 'Wrong test key <wrong@example.invalid>',
                                        'ed25519', 'sign', '0'], env=env, check=True, capture_output=True)
                        self.key.write_bytes(subprocess.check_output(['gpg', '--export'], env=env))
                        subprocess.run(['gpgconf', '--kill', 'gpg-agent'], env=env, check=True)
                try:
                    result = self.finalize()
                    self.assertNotEqual(result.returncode, 0, result.stdout)
                    self.assertIn('apt archive refused', result.stderr)
                    self.assertFalse((self.repo / 'dists/stable/InRelease').exists())
                    self.assertFalse((self.repo / 'dists/stable/Release.gpg').exists())
                finally:
                    if case == 'changed-pool':
                        pooldeb.write_bytes(original)


if __name__ == '__main__':
    unittest.main()
