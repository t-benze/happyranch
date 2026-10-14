"""Offline, exact-identity notice fallback for the two Mac packaging collectors."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

_MAP_SHA256 = 'ed3bbad5369ecf0534d080200bb0c5974342d09298bdb8d7e00d06c309581236'


def safe_path(path: Path, boundary: Path) -> None:
    """Reject lexical escapes and symlinks, including ancestor symlinks."""
    if (not path.is_absolute() or '..' in path.parts or not boundary.is_absolute()
            or not path.is_relative_to(boundary) or path.resolve() != path
            or boundary.resolve() != boundary or path.is_symlink()):
        raise ValueError('NOTICE_PATH_UNSAFE: ' + str(path))


def safe_bytes(path: Path, boundary: Path) -> bytes:
    safe_path(path, boundary)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 262144:
            raise ValueError('NOTICE_INPUT_UNVERIFIABLE: ' + str(path))
        data = stream.read(262145)
    if not data or len(data) > 262144:
        raise ValueError('NOTICE_INPUT_UNVERIFIABLE: ' + str(path))
    return data


def identities(data_root: Path) -> dict:
    data = safe_bytes(data_root / 'identity-map.json', data_root)
    if hashlib.sha256(data).hexdigest() != _MAP_SHA256:
        raise ValueError('NOTICE_IDENTITY_MAP_HASH_MISMATCH')
    return json.loads(data)


def original(data_root: Path, record: dict) -> Path:
    path = data_root / record['file']
    data = safe_bytes(path, data_root)
    data.decode('utf-8')
    if len(data) != record['size'] or hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('NOTICE_ORIGINAL_HASH_MISMATCH')
    return path


def retain(src: Path, source_root: Path, target: Path, target_root: Path) -> dict:
    """Preserve originals and refuse destination conflicts without overwriting."""
    data = safe_bytes(src, source_root)
    data.decode('utf-8')
    digest = hashlib.sha256(data).hexdigest()
    safe_path(target, target_root)
    if target.exists():
        if safe_bytes(target, target_root) != data:
            raise ValueError('NOTICE_DESTINATION_CONFLICT')
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        safe_path(target, target_root)
        with target.open('xb') as stream:
            stream.write(data)
    retained = hashlib.sha256(safe_bytes(target, target_root)).hexdigest()
    if retained != digest:
        raise ValueError('NOTICE_RETAINED_HASH_MISMATCH')
    return {'source': str(src), 'source_sha256': digest, 'sha256': retained,
            'destination': target.relative_to(target_root).as_posix()}


def python_original(data_root: Path, effective: dict, toolchain: dict,
                    stdlib: Path, analysis: dict) -> tuple[Path, dict]:
    record = identities(data_root)['python']
    if (effective.get('implementation') != record['implementation']
            or any(effective.get(k) != v for k, v in record['toolchain'].items())
            or any(toolchain.get(k) != effective.get(k)
                   for k in ('executable', 'version', 'platform', 'machine'))):
        raise ValueError('PYTHON_NOTICE_IDENTITY_MISMATCH')
    checked = []
    for filename, digest in record['stdlib'].items():
        path = stdlib / filename
        data = safe_bytes(path, stdlib)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('PYTHON_NOTICE_STDLIB_MISMATCH: ' + filename)
        for lane in ('daemon', 'cli'):
            rows = [r for r in analysis[lane]['pure'] if r['destination'] == Path(filename).stem]
            if (len(rows) != 1 or rows[0]['kind'] != 'PYMODULE'
                    or Path(rows[0]['source']) != path or rows[0]['sha256'] != digest):
                raise ValueError('PYTHON_NOTICE_ANALYSIS_MISMATCH: ' + lane + '/' + filename)
        checked.append({'source': str(path), 'sha256': digest})
    return original(data_root, record['notice']), {
        'origin': 'authenticated_offline_original', **record['notice'],
        'identity': effective, 'stdlib': checked, 'identity_map_sha256': _MAP_SHA256}


def radix_original(data_root: Path, name: str, version: str, root: Path,
                   inputs: list[dict]) -> tuple[Path, dict] | None:
    record = identities(data_root)['radix']
    matches = [p for p in record['packages'] if (p['name'], p['version']) == (name, version)]
    if not matches:
        return None
    package = matches[0]
    if root.name != name.split('/')[-1] or root.parent.name != '@radix-ui':
        raise ValueError('RADIX_NOTICE_PACKAGE_PATH_MISMATCH')
    metadata = root / 'package.json'
    data = safe_bytes(metadata, root)
    if (hashlib.sha256(data).hexdigest() != package['metadata_sha256']
            or (json.loads(data).get('name'), json.loads(data).get('version')) != (name, version)):
        raise ValueError('RADIX_NOTICE_METADATA_MISMATCH')
    if not inputs:
        raise ValueError('RADIX_NOTICE_EMITTED_INPUT_MISSING')
    for inclusion in inputs:
        path = Path(inclusion['input'])
        safe_path(path, root)
        relative = path.relative_to(root).as_posix()
        expected = package['emitted_sources'].get(relative)
        digest = hashlib.sha256(safe_bytes(path, root)).hexdigest()
        if expected is None or digest != expected or inclusion['source_sha256'] != digest:
            raise ValueError('RADIX_NOTICE_EMITTED_SOURCE_MISMATCH: ' + relative)
        # Additional anonymous/named subpath metadata is not the authenticated
        # package: never let a new manifest inherit the pinned root's fallback.
        chain = inclusion['metadata_chain']
        if (len(chain) != 1 or Path(chain[0]['path']) != metadata
                or chain[0]['sha256'] != package['metadata_sha256']):
            raise ValueError('RADIX_NOTICE_METADATA_CHAIN_MISMATCH')
    return original(data_root, record['notice']), {
        'origin': 'authenticated_offline_original', **record['notice'],
        'package': package, 'version_relation': record['version_relation'],
        'identity_map_sha256': _MAP_SHA256}
