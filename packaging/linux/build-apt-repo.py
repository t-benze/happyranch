#!/usr/bin/python3
"""Prepare a static Debian archive; verify externally supplied signatures only."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
from email.utils import format_datetime
import gzip
import hashlib
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import tempfile

ARCHES = ('amd64', 'arm64')
POOL = Path('pool/main/h/happyranch')
SUITE = Path('dists/stable')
KEYRING = 'happyranch-archive-keyring.gpg'
PAGES_URL = 'https://FOUNDER-SET-OWNER.github.io/FOUNDER-SET-REPOSITORY'
INSTALL = f"""HappyRanch apt archive

FOUNDER: replace this placeholder Pages URL before publishing:
{PAGES_URL}
Publish this entire finalized directory as static files, including .nojekyll.
Publish only after both signature verifications succeed. See the release handbook.

Users: obtain the archive public key and confirm its fingerprint through the
founder's trusted channel before installing it. Never use trusted=yes.

  curl -fsSLo happyranch-archive-keyring.gpg {PAGES_URL}/{KEYRING}
  gpg --show-keys --with-fingerprint happyranch-archive-keyring.gpg
  sudo install -d -m 0755 /etc/apt/keyrings
  sudo install -m 0644 happyranch-archive-keyring.gpg /etc/apt/keyrings/{KEYRING}

Create /etc/apt/sources.list.d/happyranch.sources with these deb822 fields
(replace the placeholder URI with the founder's published URL):

Types: deb
URIs: {PAGES_URL}
Suites: stable
Components: main
Architectures: amd64 arm64
Signed-By: /etc/apt/keyrings/{KEYRING}

  sudo apt update
  sudo apt install happyranch

Quit HappyRanch before updating or removing it:
  sudo apt update
  sudo apt upgrade
  sudo apt remove happyranch

Purge also preserves user app data. Remove the sources file and dedicated
keyring manually when no longer using this archive. The .deb installs neither.
"""


def run(argv: list[str], *, cwd: Path | None = None) -> bytes:
    return subprocess.check_output(argv, cwd=cwd)


def digest(data: bytes, algorithm: str = 'sha256') -> str:
    return hashlib.new(algorithm, data).hexdigest()


def check_tree(repo: Path) -> None:
    if not repo.is_absolute() or repo.resolve() != repo:
        raise ValueError('--repo-dir must be a literal absolute path')
    if repo.exists():
        for path in repo.rglob('*'):
            if path.is_symlink() or not (path.is_dir() or path.is_file()):
                raise ValueError('archive must contain only regular files/directories: ' + str(path))


def package_name(deb: Path) -> str:
    fields = run(['dpkg-deb', '-f', str(deb), 'Package', 'Version', 'Architecture']).decode()
    metadata = dict(line.split(': ', 1) for line in fields.splitlines())
    version, arch = metadata['Version'], metadata['Architecture']
    if (metadata['Package'] != 'happyranch' or arch not in ARCHES
            or re.fullmatch(r'[0-9][A-Za-z0-9.+:~\-]*', version) is None):
        raise ValueError('only versioned happyranch amd64/arm64 packages are supported')
    return f'happyranch_{version}_{arch}.deb'


def indexes(repo: Path) -> dict[Path, bytes]:
    # --multiversion is essential: keep older versions available for apt.
    for deb in (repo / POOL).iterdir():
        if not deb.is_file() or deb.name != package_name(deb):
            raise ValueError('unexpected pool entry: ' + str(deb))
    result = {}
    for arch in ARCHES:
        packages = run(['dpkg-scanpackages', '--multiversion', '--arch', arch,
                        str(POOL), '/dev/null'], cwd=repo)
        path = Path(f'main/binary-{arch}/Packages')
        result[path] = packages
        result[Path(str(path) + '.gz')] = gzip.compress(packages, mtime=0)
    return result


def checksums(files: dict[Path, bytes]) -> str:
    return ''.join(section + ':\n' + ''.join(
        f' {digest(data, algorithm)} {len(data):16d} {path.as_posix()}\n'
        for path, data in sorted(files.items()))
        for section, algorithm in (('MD5Sum', 'md5'), ('SHA256', 'sha256')))


def release(files: dict[Path, bytes], date: str) -> bytes:
    return ('Origin: HappyRanch\nLabel: HappyRanch\nSuite: stable\nCodename: stable\n'
            'Architectures: amd64 arm64\nComponents: main\nDate: ' + date + '\n'
            + checksums(files)).encode()


def prepare(repo: Path, debs: list[Path]) -> None:
    # Validate all supplied metadata/collisions before invalidating old signatures.
    incoming = {}
    for deb in debs:
        name = package_name(deb)
        data_hash = digest(deb.read_bytes())
        target = repo / POOL / name
        if ((target.exists() and digest(target.read_bytes()) != data_hash)
                or (name in incoming and incoming[name][1] != data_hash)):
            raise ValueError('same package version/architecture has different bytes: ' + name)
        incoming[name] = (deb, data_hash)
    (repo / POOL).mkdir(parents=True, exist_ok=True)
    suite = repo / SUITE
    suite.mkdir(parents=True, exist_ok=True)
    for name in ('InRelease', 'Release.gpg'):
        (suite / name).unlink(missing_ok=True)
    for name, (deb, _) in incoming.items():
        target = repo / POOL / name
        if not target.exists():
            shutil.copyfile(deb, target)
            target.chmod(0o644)
    files = indexes(repo)
    for path, data in files.items():
        target = suite / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    old = suite / 'Release'
    date = format_datetime(datetime.now(timezone.utc), usegmt=True)
    if old.exists():
        dates = re.findall(rb'^Date: (.+)$', old.read_bytes(), re.M)
        if len(dates) == 1 and old.read_bytes() == release(files, dates[0].decode('ascii')):
            date = dates[0].decode('ascii')
    old.write_bytes(release(files, date))
    (repo / '.nojekyll').touch()
    (repo / 'README.txt').write_text(INSTALL)
    (repo / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>HappyRanch apt archive</title>'
        '<h1>HappyRanch apt archive</h1><p>Founder: fill the Pages URL in README.txt '
        'before publishing.</p><p><a href="README.txt">Install, upgrade and remove</a>'
        '</p><p><a href="' + KEYRING + '">Archive public key</a></p>\n')
    path = shlex.quote(str(old))
    print('Unsigned Release SHA256: ' + digest(old.read_bytes()))
    print('Founder: sign exactly this Release using your own key, outside this tool.')
    print('Set SIGNING_KEY to your signing-key fingerprint. Run both commands:')
    print('gpg --local-user "$SIGNING_KEY" --output ' + shlex.quote(str(suite / 'InRelease'))
          + ' --clearsign ' + path)
    print('gpg --local-user "$SIGNING_KEY" --output ' + shlex.quote(str(suite / 'Release.gpg'))
          + ' -abs ' + path)
    print('Then finalize with both signatures and a public-only gpg --export file.')


def public_key(path: Path) -> bytes:
    """Decode public armor without importing keys or consulting any GPG home."""
    data = path.read_bytes()
    if data.startswith(b'-----BEGIN PGP PUBLIC KEY BLOCK-----'):
        lines = data.decode('ascii').splitlines()
        if lines[-1] != '-----END PGP PUBLIC KEY BLOCK-----':
            raise ValueError('invalid public key armor')
        start = lines.index('') + 1
        body = lines[start:-1]
        if body and body[-1].startswith('='):
            body.pop()  # Armor CRC is optional; signatures authenticate the actual key.
        data = base64.b64decode(''.join(body), validate=True)
    # Reject secret-key packets and non-key files before passing a public keyring
    # to gpgv. Only ordinary exported public certificate packet types are allowed.
    offset, tags = 0, []
    while offset < len(data):
        header = data[offset]
        offset += 1
        if not header & 0x80:
            raise ValueError('invalid public key packet')
        if header & 0x40:
            tag = header & 0x3f
            length = data[offset]
            offset += 1
            if 192 <= length <= 223:
                length = (length - 192) * 256 + data[offset] + 192
                offset += 1
            elif length == 255:
                length = int.from_bytes(data[offset:offset + 4], 'big')
                offset += 4
            elif length >= 224:
                raise ValueError('partial public key packets are unsupported')
        else:
            tag, kind = (header >> 2) & 15, header & 3
            if kind == 3:
                raise ValueError('indeterminate public key packet')
            width = (1, 2, 4)[kind]
            length = int.from_bytes(data[offset:offset + width], 'big')
            offset += width
        if tag not in (2, 6, 13, 14, 17) or length <= 0 or offset + length > len(data):
            raise ValueError('expected a public-only exported OpenPGP key')
        tags.append(tag)
        offset += length
    if not tags or tags[0] != 6:
        raise ValueError('expected an exported OpenPGP public certificate')
    return data


def finalize(repo: Path, inrelease: Path, detached: Path, key: Path) -> None:
    suite = repo / SUITE
    unsigned = (suite / 'Release').read_bytes()
    # Snapshot external inputs; gpgv never opens the caller's keyring/home.
    signatures = [inrelease.read_bytes(), detached.read_bytes()]
    certificate = public_key(key)
    with tempfile.TemporaryDirectory(prefix='happyranch-apt-verify-') as temporary:
        work = Path(temporary)
        (work / 'public.gpg').write_bytes(certificate)
        (work / 'Release').write_bytes(unsigned)
        for name, data in zip(('InRelease', 'Release.gpg'), signatures):
            (work / name).write_bytes(data)
        common = ['gpgv', '--homedir', str(work), '--keyring', str(work / 'public.gpg')]
        run(common + ['--output', str(work / 'cleartext'), str(work / 'InRelease')])
        if (work / 'cleartext').read_bytes() != unsigned:
            raise ValueError('InRelease does not contain the current Release')
        run(common + [str(work / 'Release.gpg'), str(work / 'Release')])
    files = indexes(repo)
    for path, data in files.items():
        if (suite / path).read_bytes() != data:
            raise ValueError('indexes differ from the pool; prepare and sign again')
    dates = re.findall(rb'^Date: (.+)$', unsigned, re.M)
    if len(dates) != 1 or unsigned != release(files, dates[0].decode('ascii')):
        raise ValueError('Release differs from current index checksums; prepare and sign again')
    # No publication happens here. Caller must keep prepare/finalize exclusive
    # and publish only after this command succeeds (see handbook).
    (repo / KEYRING).write_bytes(certificate)
    for name, data in zip(('InRelease', 'Release.gpg'), signatures):
        (suite / name).write_bytes(data)
    for path in repo.rglob('*'):
        path.chmod(0o755 if path.is_dir() else 0o644)
    repo.chmod(0o755)
    print('Verified both signatures, Release, indexes and pool. Ready for founder publication: ' + str(repo))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prep = commands.add_parser('prepare', help='add packages and regenerate unsigned indexes/Release')
    prep.add_argument('--repo-dir', type=Path, required=True)
    prep.add_argument('debs', type=Path, nargs='+')
    finish = commands.add_parser('finalize', help='verify external signatures with a public-only key')
    finish.add_argument('--repo-dir', type=Path, required=True)
    finish.add_argument('--inrelease', type=Path, required=True)
    finish.add_argument('--release-gpg', type=Path, required=True)
    finish.add_argument('--public-key', type=Path, required=True)
    args = parser.parse_args()
    try:
        check_tree(args.repo_dir)
        if args.command == 'prepare':
            prepare(args.repo_dir, args.debs)
        else:
            finalize(args.repo_dir, args.inrelease, args.release_gpg, args.public_key)
    except (OSError, ValueError, KeyError, IndexError, subprocess.CalledProcessError) as exc:
        parser.exit(1, 'apt archive refused: ' + str(exc) + '\n')


if __name__ == '__main__':
    main()
