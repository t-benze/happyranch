"""Acquire only the existing fourteen pinned web tool originals for packaging."""
import argparse
import base64
import hashlib
import json
import runpy
import urllib.parse
import urllib.request
from pathlib import Path


def prepare(source, destination):
    helper = runpy.run_path(str(source / 'app/mac/scripts/manifest_notices.py'))
    lock = json.loads((source / 'web/package-lock.json').read_text())['packages']
    upstream = json.loads((source / 'web/vendor/vite/UPSTREAM.json').read_text())
    if (not destination.is_absolute() or destination.resolve() != destination
            or destination.exists() or destination.is_symlink()
            or destination.is_relative_to(source) or source.is_relative_to(destination)):
        raise ValueError('WEB_ORIGINAL_DESTINATION_UNSAFE: use a new disjoint directory')
    destination.mkdir(mode=0o700)
    for package, version in helper['WEB_TOOLS'].items():
        entry = lock['node_modules/' + package]
        url = 'https://registry.npmjs.org/vite/-/vite-5.4.21.tgz' if package == 'vite' else entry['resolved']
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != 'https' or parsed.netloc != 'registry.npmjs.org':
            raise ValueError('WEB_ORIGINAL_URL_UNSUPPORTED: ' + package)
        if package != 'vite' and entry['version'] != version:
            raise ValueError('WEB_ORIGINAL_VERSION_MISMATCH: ' + package)
        with urllib.request.urlopen(url, timeout=60) as response:
            data = response.read(32 * 1024 * 1024 + 1)
        if not data or len(data) > 32 * 1024 * 1024:
            raise ValueError('WEB_ORIGINAL_SIZE: ' + package)
        valid = hashlib.sha256(data).hexdigest() == upstream['original_sha256'] if package == 'vite' else 'sha512-' + base64.b64encode(hashlib.sha512(data).digest()).decode() == entry['integrity']
        if not valid:
            raise ValueError('WEB_ORIGINAL_DIGEST_MISMATCH: ' + package)
        with (destination / (package.replace('/', '_') + '.tgz')).open('xb') as stream:
            stream.write(data)
    print(json.dumps({'originals': str(destination), 'count': len(helper['WEB_TOOLS']), 'status': 'hash-verified; original notice inputs only; native build still required'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--destination', type=Path, required=True)
    args = parser.parse_args()
    prepare(args.source.resolve(), args.destination)
