"""Private bounded K1 object mechanics, with no mounted access entry point.

Only a future authenticated producer may select the server records/config for
_resolve_objects. These raw mechanics do not grant adoption or content access.
No Git command ever sees the adopted repository config, refs or working tree.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zlib
from contextlib import ExitStack, contextmanager
from collections.abc import Iterator


def _git_child() -> None:
    """Isolated stdlib-only exec shim: limits set before Git, no preexec/thread race."""
    import resource
    if (len(sys.argv) != 5 or sys.argv[1] != '--git-child'
            or sys.argv[3] not in ('sha1', 'sha256')
            or sys.argv[4] not in ('check', 'body')):
        raise SystemExit(2)
    resource.setrlimit(resource.RLIMIT_AS, (134217728, 134217728))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (32, 32))
    option = '--batch-check' if sys.argv[4] == 'check' else '--batch'
    argv = ['/usr/bin/git', '--no-replace-objects', '--git-dir=' + sys.argv[2],
        '-c', 'core.hooksPath=/dev/null', '-c', 'credential.helper=',
        '-c', 'core.attributesFile=/dev/null', '-c', 'protocol.allow=never',
        '-c', 'gc.auto=0', 'cat-file', option]
    os.execve('/usr/bin/git', argv, _git_environment())


def _git_environment() -> dict[str, str]:
    return {'PATH': '/usr/bin:/bin', 'LANG': 'C', 'LC_ALL': 'C',
        'HOME': '/nonexistent', 'XDG_CONFIG_HOME': '/nonexistent',
        'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_SYSTEM': '/dev/null',
        'GIT_CONFIG_GLOBAL': '/dev/null', 'GIT_TERMINAL_PROMPT': '0',
        'GIT_NO_REPLACE_OBJECTS': '1', 'GIT_NO_LAZY_FETCH': '1',
        'GIT_OPTIONAL_LOCKS': '0', 'GIT_ATTR_NOSYSTEM': '1',
        'GIT_ALLOW_PROTOCOL': '', 'GIT_EXEC_PATH': '/nonexistent'}


if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == '--git-child':
    _git_child()
    raise SystemExit(2)

if __name__ == '__main__':
    # The reader worker is isolated from user site/env and uses this installed
    # source's fixed package root, never a selected repository helper path.
    import resource
    resource.setrlimit(resource.RLIMIT_AS, (268435456, 268435456))
    resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    sys.path.insert(0, str(Path(__file__).absolute().parents[2]))

# The child above uses only stdlib before exec; it never imports the product.
from runtime.orchestrator._paths import OrgPaths
from runtime.workflows.artifacts import CodeChangeDescriptor
from runtime.workflows.repository_artifacts import (
    BLOB_LIMIT, COMMIT_LIMIT, COPY_LIMIT, DEADLINE_SECONDS, DEPTH_LIMIT,
    OBJECT_LIMIT, PATH_LIMIT, PRESENTATION_LIMIT, READ_LIMIT, TREE_LIMIT,
    RepositoryAdoption, RepositoryAdoptionProof, RepositoryBinding,
    RepositoryPresentation, RepositoryResolution, WorkflowRepositoryError,
    _canonical, _digest, _match_adoption, _object_hash, _oid, _path, _text, _pairs,
)

_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
_REAP_RESERVE = 0.1


class _Budget:
    def __init__(self, deadline: float | None = None) -> None:
        self.deadline = time.monotonic() + DEADLINE_SECONDS if deadline is None else deadline
        self.copied = 0
        self.inflated = 0
        self.reconstructed = 0
        self.read = 0
        self.materialized = 0
        self.objects = 0
        self.listed = 0

    def check(self) -> None:
        if time.monotonic() >= self.deadline - _REAP_RESERVE:
            raise WorkflowRepositoryError('content_bounds')

    def charge(self, name: str, count: int, limit: int) -> None:
        self.check()
        total = getattr(self, name) + count
        if total > limit:
            raise WorkflowRepositoryError('content_bounds')
        if name in ('inflated', 'read'):
            self.materialized += count
            if self.materialized > READ_LIMIT:
                raise WorkflowRepositoryError('content_bounds')
        setattr(self, name, total)


def _same(a: os.stat_result, b: os.stat_result) -> bool:
    return (a.st_dev, a.st_ino, a.st_mode, a.st_size, a.st_mtime_ns, a.st_ctime_ns) == (
        b.st_dev, b.st_ino, b.st_mode, b.st_size, b.st_mtime_ns, b.st_ctime_ns)


@contextmanager
def _directory(parent: int, name: str, budget: _Budget) -> Iterator[int]:
    budget.check()
    try:
        fd = os.open(name, _DIR_FLAGS, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise WorkflowRepositoryError('content_unsupported') from None
        raise
    try:
        yield fd
        budget.check()
    finally:
        os.close(fd)


def _regular(parent: int, name: str, budget: _Budget, limit: int, *, copied: bool = False) -> bytes:
    budget.check()
    try:
        fd = os.open(name, _FILE_FLAGS, dir_fd=parent)
    except OSError as error:
        if error.errno in (errno.ELOOP, errno.ENOTDIR):
            raise WorkflowRepositoryError('content_unsupported') from None
        raise
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
            raise WorkflowRepositoryError('content_unsupported')
        if before.st_size > limit:
            raise WorkflowRepositoryError('content_bounds')
        if copied:
            budget.charge('copied', before.st_size, COPY_LIMIT)
        parts = []
        left = before.st_size
        while left:
            budget.check()
            part = os.read(fd, min(65536, left))
            if not part:
                raise WorkflowRepositoryError('storage_corrupt')
            left -= len(part)
            parts.append(part)
        if os.read(fd, 1) or not _same(before, os.fstat(fd)):
            raise WorkflowRepositoryError('storage_corrupt')
        # Verify the name still denotes the exact opened regular file.
        if not _same(before, os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise WorkflowRepositoryError('storage_corrupt')
        return b''.join(parts)
    finally:
        os.close(fd)


def _names(fd: int, budget: _Budget) -> list[str]:
    names = []
    with os.scandir(fd) as iterator:
        for entry in iterator:
            budget.charge('listed', 1, OBJECT_LIMIT * 4)
            if entry.is_symlink():
                raise WorkflowRepositoryError('content_unsupported')
            names.append(entry.name)
    return sorted(names)


def _absent(fd: int, name: str, budget: _Budget) -> None:
    budget.check()
    try:
        os.stat(name, dir_fd=fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise WorkflowRepositoryError('content_unsupported')


def _config(raw: bytes, object_format: str) -> str:
    """A closed ordinary-clone subset, never Git config/helper evaluation.

    Unknown keys/sections, includes, continuations, duplicate values and helper
    paths refuse. Exact parsed origin bytes must match server configuration.
    """
    try:
        text = raw.decode('utf8')
    except UnicodeDecodeError:
        raise WorkflowRepositoryError('content_unsupported') from None
    if '\x00' in text or text.startswith('\ufeff'):
        raise WorkflowRepositoryError('content_unsupported')
    values = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('['):
            match = re.fullmatch(r'\[(core|extensions|remote|branch)(?: "([^"\\\x00-\x1f]{1,256})")?\]', line, re.I)
            if match is None:
                raise WorkflowRepositoryError('content_unsupported')
            name, qualifier = match.groups()
            name = name.lower()
            if ((name in ('core', 'extensions') and qualifier is not None)
                    or (name == 'remote' and qualifier != 'origin')
                    or (name == 'branch' and qualifier is None)):
                raise WorkflowRepositoryError('content_unsupported')
            section = (name, qualifier)
            continue
        match = re.fullmatch(r'([a-zA-Z][a-zA-Z0-9-]*)\s*=\s*(.*)', line)
        if section is None or match is None:
            raise WorkflowRepositoryError('content_unsupported')
        key, value = match.groups()
        key = key.lower()
        allowed = {'core': {'repositoryformatversion', 'filemode', 'bare', 'logallrefupdates',
                            'ignorecase', 'precomposeunicode'},
                   'extensions': {'objectformat'}, 'remote': {'url', 'fetch'},
                   'branch': {'remote', 'merge'}}[section[0]]
        identity = (*section, key)
        if key not in allowed or identity in values or '\\' in value:
            raise WorkflowRepositoryError('content_unsupported')
        # Quoted simple values are supported; escapes and inline comments refuse.
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif any(c in value for c in ('"', '#', ';')):
            raise WorkflowRepositoryError('content_unsupported')
        if not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise WorkflowRepositoryError('content_unsupported')
        values[identity] = value
    version = values.get(('core', None, 'repositoryformatversion'))
    if (version != ('0' if object_format == 'sha1' else '1')
            or values.get(('core', None, 'bare')) != 'false'
            or values.get(('extensions', None, 'objectformat'), 'sha1') != object_format):
        raise WorkflowRepositoryError('content_unsupported')
    origin = values.get(('remote', 'origin', 'url'))
    if origin is None:
        raise WorkflowRepositoryError('binding_unavailable')
    return origin


@contextmanager
def _store(paths: OrgPaths, binding: RepositoryBinding, proof: RepositoryAdoptionProof,
           configured_origin: str, budget: _Budget) -> Iterator[int]:
    """Open every absolute component no-follow; no resolve()/common-dir fallback."""
    if (type(paths) is not OrgPaths or not paths.workspaces_dir.is_absolute()
            or paths.root.name != binding.org_slug
            or proof.repository_id != binding.repository_id
            or _digest(configured_origin.encode('utf8')) != binding.origin_digest):
        raise WorkflowRepositoryError('binding_unavailable')
    with ExitStack() as stack:
        fd = os.open('/', _DIR_FLAGS)
        stack.callback(os.close, fd)
        for part in paths.workspaces_dir.parts[1:] + (binding.store_owner, 'repos', binding.repo_key):
            if part in ('', '.', '..'):
                raise WorkflowRepositoryError('content_unsupported')
            fd = stack.enter_context(_directory(fd, part, budget))
        repo_fd = fd
        git_fd = stack.enter_context(_directory(repo_fd, '.git', budget))
        objects_fd = stack.enter_context(_directory(git_fd, 'objects', budget))
        for name in ('commondir', 'gitdir', 'shallow'):
            _absent(git_fd, name, budget)
        try:
            packed_refs = _regular(git_fd, 'packed-refs', budget, COMMIT_LIMIT)
        except FileNotFoundError:
            packed_refs = b''
        if b' refs/replace/' in packed_refs:
            raise WorkflowRepositoryError('content_unsupported')
        for name, forbidden in (('info', 'grafts'), ('refs', 'replace')):
            try:
                child = stack.enter_context(_directory(git_fd, name, budget))
            except FileNotFoundError:
                continue
            _absent(child, forbidden, budget)
        config = _regular(git_fd, 'config', budget, COMMIT_LIMIT)
        if _config(config, proof.object_format) != configured_origin:
            raise WorkflowRepositoryError('binding_unavailable')
        identity = proof.store_identity
        for opened, device, inode in ((repo_fd, identity.repo_device, identity.repo_inode),
                (git_fd, identity.git_device, identity.git_inode),
                (objects_fd, identity.objects_device, identity.objects_inode)):
            current = os.fstat(opened)
            if (current.st_dev, current.st_ino) != (device, inode):
                raise WorkflowRepositoryError('binding_unavailable')
        if _digest(config) != identity.config_digest:
            raise WorkflowRepositoryError('binding_unavailable')
        yield objects_fd
        if _regular(git_fd, 'config', budget, COMMIT_LIMIT) != config:
            raise WorkflowRepositoryError('binding_unavailable')


def _inflate(data: bytes, offset: int, limit: int, budget: _Budget) -> tuple[bytes, int]:
    decoder = zlib.decompressobj()
    chunks = []
    size = 0
    cursor = offset
    pending = b''
    while not decoder.eof:
        budget.check()
        if not pending:
            pending = data[cursor:cursor + 65536]
            cursor += len(pending)
        if not pending:
            raise WorkflowRepositoryError('storage_corrupt')
        body = decoder.decompress(pending, min(limit - size + 1, 65536))
        budget.charge('inflated', len(body), READ_LIMIT)
        size += len(body)
        if size > limit:
            raise WorkflowRepositoryError('content_bounds')
        chunks.append(body)
        pending = decoder.unconsumed_tail
        if decoder.eof:
            cursor -= len(decoder.unused_data)
    return b''.join(chunks), cursor


def _loose(data: bytes, name: str, object_format: str, budget: _Budget) -> None:
    raw, end = _inflate(data, 0, TREE_LIMIT + 64, budget)
    if end != len(data) or b'\0' not in raw:
        raise WorkflowRepositoryError('storage_corrupt')
    header, body = raw.split(b'\0', 1)
    match = re.fullmatch(rb'(commit|tree|blob|tag) (0|[1-9][0-9]{0,8})', header)
    if match is None or int(match[2]) != len(body):
        raise WorkflowRepositoryError('storage_corrupt')
    kind = match[1].decode('ascii')
    if len(body) > {'commit': COMMIT_LIMIT, 'tree': TREE_LIMIT, 'blob': BLOB_LIMIT, 'tag': COMMIT_LIMIT}[kind]:
        raise WorkflowRepositoryError('content_bounds')
    if _object_hash(kind, body, object_format) != name:
        raise WorkflowRepositoryError('storage_corrupt')


def _delta_integer(data: bytes, offset: int) -> tuple[int, int]:
    result = 0
    for shift in range(0, 35, 7):
        if offset >= len(data):
            raise WorkflowRepositoryError('storage_corrupt')
        value = data[offset]
        offset += 1
        result |= (value & 127) << shift
        if not value & 128:
            return result, offset
    raise WorkflowRepositoryError('storage_corrupt')


def _delta(data: bytes) -> int:
    """Bound reconstructed sizes and validate all instructions before Git inflates."""
    source, pos = _delta_integer(data, 0)
    target, pos = _delta_integer(data, pos)
    if source > TREE_LIMIT or target > TREE_LIMIT:
        raise WorkflowRepositoryError('content_bounds')
    written = 0
    while pos < len(data):
        op = data[pos]
        pos += 1
        if op & 128:
            offset = length = 0
            for bit in range(7):
                if op & (1 << bit):
                    if pos >= len(data):
                        raise WorkflowRepositoryError('storage_corrupt')
                    if bit < 4:
                        offset |= data[pos] << (bit * 8)
                    else:
                        length |= data[pos] << ((bit - 4) * 8)
                    pos += 1
            length = length or 65536
            if offset + length > source:
                raise WorkflowRepositoryError('storage_corrupt')
        elif op:
            length = op
            pos += length
            if pos > len(data):
                raise WorkflowRepositoryError('storage_corrupt')
        else:
            raise WorkflowRepositoryError('storage_corrupt')
        written += length
        if written > target:
            raise WorkflowRepositoryError('storage_corrupt')
    if written != target:
        raise WorkflowRepositoryError('storage_corrupt')
    return target


def _pack(data: bytes, object_format: str, budget: _Budget) -> tuple[int, set[int]]:
    hash_size = 20 if object_format == 'sha1' else 32
    if len(data) < 12 + hash_size or data[:4] != b'PACK':
        raise WorkflowRepositoryError('storage_corrupt')
    version, count = struct.unpack('>II', data[4:12])
    if version not in (2, 3) or count > OBJECT_LIMIT:
        raise WorkflowRepositoryError('content_bounds')
    if hashlib.new(object_format, data[:-hash_size]).digest() != data[-hash_size:]:
        raise WorkflowRepositoryError('storage_corrupt')
    budget.charge('objects', count, OBJECT_LIMIT)
    pos = 12
    offsets = set()
    for _ in range(count):
        budget.check()
        start = pos
        offsets.add(start)
        if pos >= len(data) - hash_size:
            raise WorkflowRepositoryError('storage_corrupt')
        first = data[pos]
        pos += 1
        kind, size = (first >> 4) & 7, first & 15
        byte = first
        shift = 4
        while byte & 128:
            if pos >= len(data) - hash_size or shift > 32:
                raise WorkflowRepositoryError('storage_corrupt')
            byte = data[pos]
            pos += 1
            size |= (byte & 127) << shift
            shift += 7
        if kind not in (1, 2, 3, 4, 6, 7):
            raise WorkflowRepositoryError('storage_corrupt')
        if size > {1: COMMIT_LIMIT, 2: TREE_LIMIT, 3: BLOB_LIMIT,
                   4: COMMIT_LIMIT, 6: TREE_LIMIT, 7: TREE_LIMIT}[kind]:
            raise WorkflowRepositoryError('content_bounds')
        if kind == 6:
            distance = 0
            for _ in range(5):
                if pos >= len(data) - hash_size:
                    raise WorkflowRepositoryError('storage_corrupt')
                byte = data[pos]
                pos += 1
                distance = (distance << 7) | (byte & 127)
                if not byte & 128:
                    break
                distance += 1
            else:
                raise WorkflowRepositoryError('storage_corrupt')
            if not distance or start - distance not in offsets - {start}:
                raise WorkflowRepositoryError('storage_corrupt')
        elif kind == 7:
            if pos + hash_size > len(data) - hash_size:
                raise WorkflowRepositoryError('storage_corrupt')
            pos += hash_size
        body, pos = _inflate(data, pos, size, budget)
        if len(body) != size:
            raise WorkflowRepositoryError('storage_corrupt')
        budget.charge('reconstructed', _delta(body) if kind in (6, 7) else size, READ_LIMIT)
    if pos != len(data) - hash_size:
        raise WorkflowRepositoryError('storage_corrupt')
    return count, offsets


def _index(data: bytes, pack: bytes, count: int, offsets: set[int], object_format: str) -> None:
    hash_size = 20 if object_format == 'sha1' else 32
    # v2 is the only supported index layout; no ambiguous old/new fallback.
    if data[:8] != b'\xfftOc\0\0\0\x02' or len(data) < 1032 + 2 * hash_size:
        raise WorkflowRepositoryError('content_unsupported')
    fanout = struct.unpack('>256I', data[8:1032])
    if tuple(sorted(fanout)) != fanout or fanout[-1] != count:
        raise WorkflowRepositoryError('storage_corrupt')
    end_names = 1032 + count * hash_size
    end_crc = end_names + count * 4
    end_offsets = end_crc + count * 4
    if len(data) < end_offsets + 2 * hash_size:
        raise WorkflowRepositoryError('storage_corrupt')
    names = [data[i:i + hash_size] for i in range(1032, end_names, hash_size)]
    if names != sorted(set(names)):
        raise WorkflowRepositoryError('storage_corrupt')
    if any(sum(name[0] <= i for name in names) != fanout[i] for i in range(256)):
        raise WorkflowRepositoryError('storage_corrupt')
    # Copy cap <2GiB, so a genuine offset never needs the large-offset table.
    stored = struct.unpack('>' + str(count) + 'I', data[end_crc:end_offsets]) if count else ()
    if len(data) != end_offsets + 2 * hash_size or set(stored) != offsets:
        raise WorkflowRepositoryError('storage_corrupt')
    if (data[-2 * hash_size:-hash_size] != pack[-hash_size:]
            or hashlib.new(object_format, data[:-hash_size]).digest() != data[-hash_size:]):
        raise WorkflowRepositoryError('storage_corrupt')
    ordered_offsets = sorted(offsets) + [len(pack) - hash_size]
    spans = dict(zip(ordered_offsets, ordered_offsets[1:]))
    crcs = struct.unpack('>' + str(count) + 'I', data[end_names:end_crc]) if count else ()
    for offset, crc in zip(stored, crcs):
        if zlib.crc32(pack[offset:spans[offset]]) != crc:
            raise WorkflowRepositoryError('storage_corrupt')


def _write(path: Path, data: bytes, budget: _Budget) -> None:
    budget.check()
    with path.open('xb') as stream:
        stream.write(data)
    path.chmod(0o600)


def _copy(objects: int, target: Path, object_format: str, budget: _Budget) -> None:
    size = 40 if object_format == 'sha1' else 64
    for name in _names(objects, budget):
        budget.check()
        if name == 'info':
            with _directory(objects, name, budget) as info:
                if _names(info, budget):
                    # No alternates, http-alternates, commit graphs or unknown helpers.
                    raise WorkflowRepositoryError('content_unsupported')
        elif name == 'pack':
            with _directory(objects, name, budget) as packs:
                names = _names(packs, budget)
                stems = set()
                for filename in names:
                    match = re.fullmatch(r'pack-([0-9a-f]{' + str(size) + r'})\.(pack|idx)', filename)
                    if match is None:
                        raise WorkflowRepositoryError('content_unsupported')
                    stems.add('pack-' + match[1])
                for stem in sorted(stems):
                    if stem + '.pack' not in names or stem + '.idx' not in names:
                        raise WorkflowRepositoryError('storage_corrupt')
                    pack = _regular(packs, stem + '.pack', budget, COPY_LIMIT, copied=True)
                    idx = _regular(packs, stem + '.idx', budget, COPY_LIMIT, copied=True)
                    count, offsets = _pack(pack, object_format, budget)
                    _index(idx, pack, count, offsets, object_format)
                    if stem != 'pack-' + pack[-size // 2:].hex():
                        raise WorkflowRepositoryError('storage_corrupt')
                    _write(target / 'pack' / (stem + '.pack'), pack, budget)
                    _write(target / 'pack' / (stem + '.idx'), idx, budget)
        elif re.fullmatch(r'[0-9a-f]{2}', name):
            with _directory(objects, name, budget) as loose:
                destination = target / name
                destination.mkdir(mode=0o700)
                for filename in _names(loose, budget):
                    if re.fullmatch(r'[0-9a-f]{' + str(size - 2) + r'}', filename) is None:
                        raise WorkflowRepositoryError('content_unsupported')
                    budget.charge('objects', 1, OBJECT_LIMIT)
                    raw = _regular(loose, filename, budget, COPY_LIMIT, copied=True)
                    _loose(raw, name + filename, object_format, budget)
                    _write(destination / filename, raw, budget)
        else:
            raise WorkflowRepositoryError('content_unsupported')


def _run_git(directory: Path, object_format: str, oid: str, mode: str,
             output_limit: int, budget: _Budget) -> bytes:
    budget.check()
    process = None
    output = bytearray()
    error_size = 0
    try:
        process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).absolute()),
            '--git-child', str(directory), object_format, mode], cwd=directory,
            env=_git_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True)
        process.stdin.write(oid.encode('ascii') + b'\n')
        process.stdin.close()
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, 'out')
            selector.register(process.stderr, selectors.EVENT_READ, 'err')
            while selector.get_map():
                budget.check()
                remaining = budget.deadline - _REAP_RESERVE - time.monotonic()
                for key, _ in selector.select(max(0, remaining)):
                    budget.check()
                    raw = os.read(key.fileobj.fileno(), 65536)
                    if not raw:
                        selector.unregister(key.fileobj)
                    elif key.data == 'out':
                        if len(output) + len(raw) > output_limit:
                            raise WorkflowRepositoryError('content_bounds')
                        output.extend(raw)
                    else:
                        error_size += len(raw)
                        if error_size > 4096:
                            raise WorkflowRepositoryError('content_bounds')
        status = process.wait(timeout=max(0, budget.deadline - time.monotonic()))
        if status != 0:
            raise WorkflowRepositoryError('object_unavailable')
        budget.check()
        return bytes(output)
    finally:
        if process is None:
            # Interrupted launch has no affirmative no-launch/reap witness.
            # Retain the reader's group owner even if the nested PID is unknown.
            raise WorkflowRepositoryError('storage_unavailable', execution_unknown=True,
                process_id=os.getpid(), private_storage=str(directory)) from None
        try:
            try:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=max(0, budget.deadline - time.monotonic()))
            finally:
                for stream in (process.stdin, process.stdout, process.stderr):
                    if stream is not None:
                        stream.close()
        except BaseException:
            # Stream-close failure must not erase unknown nested reap ownership.
            # Retain exact private owner and storage; do not imply quiescence.
            raise WorkflowRepositoryError('storage_unavailable', execution_unknown=True,
                process_id=process.pid, private_storage=str(directory)) from None


class _Objects:
    def __init__(self, directory: Path, object_format: str, budget: _Budget) -> None:
        self.directory = directory
        self.object_format = object_format
        self.budget = budget
        self.cache = {}
        self.requests = 0

    def read(self, oid: str, kind: str) -> bytes:
        self.budget.check()
        _oid(oid, self.object_format)
        self.requests += 1
        if self.requests > OBJECT_LIMIT:
            raise WorkflowRepositoryError('content_bounds')
        if oid in self.cache:
            found_kind, raw = self.cache[oid]
            if found_kind != kind:
                raise WorkflowRepositoryError('object_type_invalid')
            return raw
        header = _run_git(self.directory, self.object_format, oid, 'check', 256, self.budget)
        if header == oid.encode('ascii') + b' missing\n':
            raise WorkflowRepositoryError('object_unavailable')
        match = re.fullmatch(rb'([0-9a-f]+) (commit|tree|blob|tag) (0|[1-9][0-9]{0,8})\n', header)
        if match is None or match[1].decode() != oid:
            raise WorkflowRepositoryError('storage_corrupt')
        if match[2].decode() != kind:
            raise WorkflowRepositoryError('object_type_invalid')
        size = int(match[3])
        if size > {'commit': COMMIT_LIMIT, 'tree': TREE_LIMIT, 'blob': BLOB_LIMIT}[kind]:
            raise WorkflowRepositoryError('content_bounds')
        self.budget.charge('read', size, READ_LIMIT)
        result = _run_git(self.directory, self.object_format, oid, 'body', size + 256, self.budget)
        prefix = header
        if not result.startswith(prefix) or len(result) != len(prefix) + size + 1 or result[-1:] != b'\n':
            raise WorkflowRepositoryError('storage_corrupt')
        raw = result[len(prefix):-1]
        if _object_hash(kind, raw, self.object_format) != oid:
            raise WorkflowRepositoryError('storage_corrupt')
        self.cache[oid] = (kind, raw)
        return raw

    def tree(self, commit: str) -> dict[str, tuple[str, str, bytes]]:
        raw = self.read(commit, 'commit')
        headers = raw.split(b'\n\n', 1)
        oid_size = 40 if self.object_format == 'sha1' else 64
        if len(headers) != 2 or not re.fullmatch(rb'tree [0-9a-f]{' + str(oid_size).encode() + rb'}',
                                               headers[0].split(b'\n', 1)[0]):
            raise WorkflowRepositoryError('storage_corrupt')
        if sum(line.startswith(b'tree ') for line in headers[0].split(b'\n')) != 1:
            raise WorkflowRepositoryError('storage_corrupt')
        root = headers[0].split(b'\n', 1)[0][5:].decode('ascii')
        result = {}
        stack = [(root, '', 0, frozenset())]
        while stack:
            self.budget.check()
            oid, prefix, depth, ancestors = stack.pop()
            if depth >= DEPTH_LIMIT or oid in ancestors:
                raise WorkflowRepositoryError('content_bounds')
            tree = self.read(oid, 'tree')
            if not tree and prefix:
                # Empty nested trees carry a directory change that the closed
                # regular-blob manifest cannot represent; never silently skip.
                raise WorkflowRepositoryError('content_unsupported')
            pos = 0
            previous = None
            names = set()
            while pos < len(tree):
                self.budget.check()
                space = tree.find(b' ', pos)
                nul = tree.find(b'\0', space + 1) if space >= 0 else -1
                if space < 0 or nul < 0 or nul + 1 + oid_size // 2 > len(tree):
                    raise WorkflowRepositoryError('storage_corrupt')
                mode = tree[pos:space]
                name = tree[space + 1:nul]
                if mode not in (b'40000', b'100644', b'100755'):
                    raise WorkflowRepositoryError('content_unsupported')
                try:
                    text = name.decode('utf8')
                except UnicodeDecodeError:
                    raise WorkflowRepositoryError('content_unsupported') from None
                if '/' in text:
                    raise WorkflowRepositoryError('content_unsupported')
                path = prefix + text
                try:
                    _path(path)
                except ValueError:
                    raise WorkflowRepositoryError('content_unsupported') from None
                ordering = name + (b'/' if mode == b'40000' else b'\0')
                if name in names or (previous is not None and ordering <= previous):
                    raise WorkflowRepositoryError('storage_corrupt')
                names.add(name)
                previous = ordering
                child = tree[nul + 1:nul + 1 + oid_size // 2].hex()
                pos = nul + 1 + oid_size // 2
                if mode == b'40000':
                    stack.append((child, path + '/', depth + 1, ancestors | {oid}))
                    if len(stack) + self.requests > OBJECT_LIMIT:
                        raise WorkflowRepositoryError('content_bounds')
                else:
                    blob = self.read(child, 'blob')
                    try:
                        _text(blob)
                    except ValueError:
                        raise WorkflowRepositoryError('content_unsupported') from None
                    if path in result:
                        raise WorkflowRepositoryError('storage_corrupt')
                    result[path] = (mode.decode('ascii'), child, blob)
        return result


def _build_resolution(*, paths: OrgPaths, binding: RepositoryBinding,
                     adoption: RepositoryAdoption, proof: RepositoryAdoptionProof,
                     descriptor: CodeChangeDescriptor, configured_origin: str,
                     private_directory: Path, deadline: float) -> RepositoryResolution:
    """Raw internal mechanics ONLY, no access verifier or adoption issuer.

    Server-owned selection must precede this call in K2b/K3. The current mount
    cannot reach it. A saved record or caller dict must never authorize it.
    """
    budget = _Budget(deadline)
    private = private_directory
    retain = False
    try:
        _match_adoption(binding, adoption, proof)
        if (binding.repository_id != descriptor.repository.id
                or proof.object_format != descriptor.object_format
                or len(_canonical(descriptor.model_dump())) > 1048576):
            raise WorkflowRepositoryError('binding_unavailable')
        # Known installed tool only, no PATH discovery or configurable helper.
        git_stat = os.lstat('/usr/bin/git')
        if not stat.S_ISREG(git_stat.st_mode) or not os.access('/usr/bin/git', os.X_OK):
            raise WorkflowRepositoryError('reader_unavailable')
        private.chmod(0o700)
        (private / 'objects' / 'pack').mkdir(mode=0o700, parents=True)
        (private / 'objects' / 'info').mkdir(mode=0o700)
        (private / 'refs').mkdir(mode=0o700)
        _write(private / 'HEAD', b'ref: refs/heads/unselected\n', budget)
        config = b'[core]\nrepositoryformatversion = ' + (b'0' if proof.object_format == 'sha1' else b'1')
        config += b'\nbare = true\n'
        if proof.object_format == 'sha256':
            config += b'[extensions]\nobjectformat = sha256\n'
        _write(private / 'config', config, budget)
        with _store(paths, binding, proof, configured_origin, budget) as objects:
            _copy(objects, private / 'objects', proof.object_format, budget)
        reader = _Objects(private, proof.object_format, budget)
        head = reader.tree(descriptor.head)
        base_oid = None if descriptor.comparison is None else descriptor.comparison.base
        base = {} if base_oid is None else reader.tree(base_oid)
        changed = sorted((path for path in head.keys() | base.keys() if head.get(path) != base.get(path)),
                         key=lambda path: path.encode('utf8'))
        if len(changed) > PATH_LIMIT:
            raise WorkflowRepositoryError('content_bounds')
        def side(value: tuple[str, str, bytes] | None) -> dict | None:
            if value is None:
                return None
            return dict(mode=value[0], blob_oid=value[1], bytes_base64=base64.b64encode(value[2]).decode('ascii'))
        value = dict(format='workflow-repository-presentation@1', repository_id=binding.repository_id,
            object_format=descriptor.object_format, head=descriptor.head, base=base_oid,
            entries=[dict(path=path, before=side(base.get(path)), after=side(head.get(path))) for path in changed])
        presentation = _canonical(value)
        if len(presentation) > PRESENTATION_LIMIT:
            raise WorkflowRepositoryError('content_bounds')
        RepositoryPresentation.model_validate(value)
        # Reopen the entire server-derived path to catch adopted directory swaps.
        # This is storage consistency, not final selected authority admission.
        with _store(paths, binding, proof, configured_origin, budget):
            pass
        budget.check()
        return RepositoryResolution(binding=binding, binding_digest=_digest(_canonical(binding.model_dump())),
            presentation_bytes_base64=base64.b64encode(presentation).decode('ascii'),
            presentation_digest=_digest(presentation))
    except WorkflowRepositoryError as error:
        retain = error.execution_unknown
        raise
    except FileNotFoundError:
        raise WorkflowRepositoryError('binding_unavailable') from None
    except (OSError, subprocess.SubprocessError):
        raise WorkflowRepositoryError('storage_unavailable') from None
    except (ValueError, zlib.error, struct.error, OverflowError):
        raise WorkflowRepositoryError('storage_corrupt') from None
    finally:
        if private is not None and not retain:
            try:
                # A deadline refusal may already have consumed the one-shot
                # alarm. Do not begin removal once its cleanup budget is gone.
                budget.check()
                shutil.rmtree(private)
            except WorkflowRepositoryError as error:
                # The alarm also bounds cleanup. Interrupted removal is not
                # absence, even though no nested process remains unknown.
                error.private_storage = str(private)
                raise
            except OSError:
                # Failed cleanup cannot masquerade as a successful resolution.
                raise WorkflowRepositoryError('storage_unavailable', private_storage=str(private)) from None


def _reader_child() -> None:
    """One private owner handles all copy/inflation/Git/tree work within D5s."""
    if len(sys.argv) != 4 or sys.argv[1] != '--reader-child':
        raise SystemExit(2)
    deadline = float(sys.argv[3])
    def expired(signum: int, frame: object) -> None:
        raise WorkflowRepositoryError('content_bounds')
    signal.signal(signal.SIGALRM, expired)
    signal.setitimer(signal.ITIMER_REAL, max(0.000001, deadline - _REAP_RESERVE - time.monotonic()))
    try:
        raw = sys.stdin.buffer.read(1048577)
        if len(raw) > 1048576:
            raise WorkflowRepositoryError('content_bounds')
        command = json.loads(raw.decode('utf8'), object_pairs_hook=_pairs)
        if (type(command) is not dict or set(command) !=
                {'org_root', 'binding', 'adoption', 'proof', 'descriptor', 'configured_origin'}
                or _canonical(command) != raw or type(command['org_root']) is not str
                or type(command['configured_origin']) is not str):
            raise WorkflowRepositoryError('storage_corrupt')
        resolution = _build_resolution(paths=OrgPaths(root=Path(command['org_root'])),
            binding=RepositoryBinding.model_validate(command['binding']),
            adoption=RepositoryAdoption.model_validate(command['adoption']),
            proof=RepositoryAdoptionProof.model_validate(command['proof']),
            descriptor=CodeChangeDescriptor.model_validate(command['descriptor']),
            configured_origin=command['configured_origin'], private_directory=Path(sys.argv[2]), deadline=deadline)
        result = dict(ok=resolution.model_dump())
    except WorkflowRepositoryError as error:
        result = dict(error=error.code, execution_unknown=error.execution_unknown,
                      process_id=error.process_id, private_storage=sys.argv[2])
    except Exception:
        # No traceback/config/content is published to the parent or a caller.
        result = dict(error='workflow_repository_storage_unavailable',
                      execution_unknown=True, process_id=os.getpid(), private_storage=sys.argv[2])
    signal.setitimer(signal.ITIMER_REAL, 0)
    sys.stdout.buffer.write(_canonical(result))
    sys.stdout.buffer.flush()


def _resolve_objects(*, paths: OrgPaths, binding: RepositoryBinding,
                     adoption: RepositoryAdoption, proof: RepositoryAdoptionProof,
                     descriptor: CodeChangeDescriptor, configured_origin: str) -> RepositoryResolution:
    """Unmounted internal mechanics, supervised across *all* work, not just Git.

    These parameters are server-selected, not public path/URL/proof inputs.
    K2b's real capability admission remains a necessary unavailable dependency.
    """
    budget = _Budget()
    command = _canonical(dict(org_root=str(paths.root), binding=binding.model_dump(),
        adoption=adoption.model_dump(), proof=proof.model_dump(), descriptor=descriptor.model_dump(),
        configured_origin=configured_origin))
    if len(command) > 1048576:
        raise WorkflowRepositoryError('content_bounds')
    private = None
    process = None
    launch_reserved = False
    quiescent = False
    failure = None
    output = bytearray()
    try:
        # Until allocation returns there is no acquired directory or launch.
        # Its OSError must cross the same category-only boundary as later I/O.
        private = Path(tempfile.mkdtemp(prefix='workflow-object-only-'))
        budget.check()
        launch_reserved = True
        process = subprocess.Popen([sys.executable, '-I', str(Path(__file__).absolute()),
            '--reader-child', str(private), str(budget.deadline)], cwd=private,
            env=_git_environment(), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, close_fds=True, start_new_session=True)
        # Nonblocking input prevents a child stuck in startup from blocking D5s.
        os.set_blocking(process.stdin.fileno(), False)
        sent = error_size = 0
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdin, selectors.EVENT_WRITE, 'in')
            selector.register(process.stdout, selectors.EVENT_READ, 'out')
            selector.register(process.stderr, selectors.EVENT_READ, 'err')
            while selector.get_map():
                remaining = budget.deadline - _REAP_RESERVE - time.monotonic()
                if remaining <= 0:
                    raise WorkflowRepositoryError('content_bounds', execution_unknown=True,
                        process_id=process.pid, private_storage=str(private))
                for key, _ in selector.select(remaining):
                    if key.data == 'in':
                        size = os.write(process.stdin.fileno(), command[sent:sent + 65536])
                        sent += size
                        if sent == len(command):
                            selector.unregister(process.stdin)
                            process.stdin.close()
                        continue
                    part = os.read(key.fileobj.fileno(), 65536)
                    if not part:
                        selector.unregister(key.fileobj)
                    elif key.data == 'out':
                        if len(output) + len(part) > 1048576:
                            raise WorkflowRepositoryError('content_bounds', execution_unknown=True,
                                process_id=process.pid, private_storage=str(private))
                        output.extend(part)
                    else:
                        error_size += len(part)
                        if error_size > 4096:
                            raise WorkflowRepositoryError('content_bounds', execution_unknown=True,
                                process_id=process.pid, private_storage=str(private))
        status = process.wait(timeout=max(0, budget.deadline - time.monotonic()))
        if status != 0:
            raise WorkflowRepositoryError('storage_unavailable', execution_unknown=True,
                process_id=process.pid, private_storage=str(private))
        answer = json.loads(bytes(output).decode('utf8'), object_pairs_hook=_pairs)
        if type(answer) is not dict or _canonical(answer) != bytes(output):
            raise ValueError('invalid_worker_reply')
        if set(answer) == {'ok'}:
            resolution = RepositoryResolution.model_validate(answer['ok'])
            try:
                os.lstat(private)
            except FileNotFoundError:
                pass
            else:
                raise ValueError('worker_cleanup_missing')
            quiescent = True
            budget.check()
            return resolution
        if (set(answer) != {'error', 'execution_unknown', 'process_id', 'private_storage'}
                or type(answer['execution_unknown']) is not bool
                or (answer['process_id'] is not None and
                    (type(answer['process_id']) is not int or answer['process_id'] <= 0))
                or answer['private_storage'] not in (None, str(private))
                or answer['error'] not in {'workflow_repository_' + c for c in
                    ('reader_unavailable', 'binding_unavailable', 'object_unavailable', 'object_type_invalid',
                     'content_unsupported', 'content_bounds', 'storage_corrupt', 'storage_unavailable')}):
            raise ValueError('invalid_worker_reply')
        quiescent = not answer['execution_unknown']
        raise WorkflowRepositoryError(answer['error'].removeprefix('workflow_repository_'),
            execution_unknown=answer['execution_unknown'], process_id=answer['process_id'],
            private_storage=str(private))
    except WorkflowRepositoryError as error:
        # The parent owns this identity. Child omission, process quiescence or
        # a refusal before _build_resolution cannot prove storage absence.
        if private is not None:
            error.private_storage = str(private)
        failure = error
        raise
    except BaseException:
        failure = WorkflowRepositoryError('storage_unavailable',
            execution_unknown=launch_reserved and not quiescent,
            process_id=None if process is None else process.pid,
            private_storage=None if private is None else str(private))
        raise failure from None
    finally:
        try:
            if process is not None:
                # Successful/known-error workers already reaped each nested Git
                # child. Unknown nested ownership survives the outer reap.
                if not quiescent:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                try:
                    process.wait(timeout=max(0, budget.deadline - time.monotonic()))
                finally:
                    for stream in (process.stdin, process.stdout, process.stderr):
                        if stream is not None:
                            stream.close()
            elif launch_reserved:
                # No returned handle proves neither no-launch nor quiescence.
                raise WorkflowRepositoryError('storage_unavailable')
        except BaseException:
            # Keep a known nested PID rather than replacing it with its reaped
            # outer worker. Every teardown failure still retains parent storage.
            owner = None if failure is None else failure.process_id
            if owner is None and process is not None:
                owner = process.pid
            raise WorkflowRepositoryError('storage_unavailable', execution_unknown=True,
                process_id=owner, private_storage=None if private is None else str(private)) from None
        # No recursive parent cleanup outside the alarm-bounded worker. Failure
        # retains the acquired identity for owner remediation, even prelaunch;
        # private_storage is not a claim that the directory is still present.


if __name__ == '__main__':
    _reader_child()
