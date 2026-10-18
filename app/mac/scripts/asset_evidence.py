#!/usr/bin/env python3
"""Private, bounded local packaging observations; never a rights/release approval.

Only build-app.sh and verify-release.sh are production callers. All expectations
come from the admitted invocation, not a candidate's stored documents. Stored
records require the original subjects and native custodian evidence to be useful.
"""
from __future__ import annotations

import argparse
import contextlib
import contextvars
from dataclasses import asdict, dataclass
import functools
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
import unicodedata
import uuid

KiB = 1024
MiB = 1024 * KiB
ENCODING = 'asset-evidence-local-v1'
CAPS = {'context': 64 * KiB, 'source': 8 * MiB, 'dist': 8 * MiB,
        'app': 8 * MiB, 'icon': 256 * KiB, 'notices': 256 * KiB}
FILES = {kind: f'assets-{kind}.json' for kind in CAPS}
FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
DIR_FLAGS = FLAGS | os.O_DIRECTORY
SUBJECT_KEYS = {'relative', 'size', 'sha256', 'dev', 'ino', 'mode', 'uid',
                'nlink', 'mtime_ns', 'ctime_ns'}
DIR_KEYS = {'relative', 'dev', 'ino', 'uid', 'mode'}
RETIRED = {'HankenGrotesk-latin.woff2', 'Newsreader-normal-latin.woff2',
           'Newsreader-italic-latin.woff2'}
MANIFEST_SHA = '82cd1aabe9295f88ce89420f8d2f3b26c1da702f1a42491771573bba385eafe0'
# Independently accepted original tuples, not destinations or digests nominated
# by a candidate manifest. Provenance remains attributed to TASK-11585/11592.
FONTS = (
    ('HankenGrotesk-wght.ttf', 132892, '813b3f8fa0965405669a89b38e51bbefd95eef6b8e20d1cb2d8c10cce062662f',
     'HankenGrotesk-OFL.txt', 4402, 'e02ccb89a86839b22feff7872ff5cc355cc0f58318d29eee20e2cf83a612f16d'),
    ('Newsreader-opsz-wght.ttf', 451664, '8a08d13f8a6c0d51be379a60af84f945f65369a67e509ee3c3bdcc421254d7c1',
     'Newsreader-OFL.txt', 4394, 'fdfad38143ec470553cae82a1e45320bdd1b9ec70415d37bd0171051d8a4ded8'),
    ('Newsreader-Italic-opsz-wght.ttf', 495684, '796668611f80b64d5adf182fde3b6f29ed83b4e7cbec7b96937e84ac01364792',
     'Newsreader-OFL.txt', 4394, 'fdfad38143ec470553cae82a1e45320bdd1b9ec70415d37bd0171051d8a4ded8'),
)
PNGS = {
    16: (653, '4ed1fb19a856e3f8ae5c447607674fdb0b43d76514574e57c34e965e3a5199fb'),
    32: (1585, '488562247a35f9df4876e8df1b6483f13a08a83f980330c6e50f33727873e720'),
    64: (3799, '06bda5120a0bb0f86cc79aae1135efbb295a3b3c2f61563980833dd9edb27d2a'),
    128: (8247, '49d0dffd9b9fec1a686deb8ee48b32d9b818ef5fb1fae253e5fd7ba54864ca25'),
    256: (19663, 'b2c23a5c965bbfdd13d1b7b62003b00eb2701d71c3db3992d9dfcc6aa2794142'),
    512: (55173, 'e568c62d0bbe8dc5822a6e041dadfa8ea1d1a663048c9f1d89ac48800b438e9a'),
    1024: (155029, '116fdfe5f04022097906844290e91ba7c6477bb6a2e125909768e8c78e6e1b68'),
}
ICON_MAP = tuple((n * multiplier, f'icon_{n}x{n}{suffix}.png')
                 for n in (16, 32, 128, 256, 512)
                 for multiplier, suffix in ((1, ''), (2, '@2x')))
ICON_BASE = 'app/mac/Resources/AppIcon.appiconset'
APP_WEB = 'Contents/Resources/web/dist'
NOTICE_BASE = 'Contents/Resources/licenses/assets'


class AssetError(ValueError):
    def __init__(self, category, subject='', *, original_exit=None, owners=()):
        self.category = category
        self.subject = str(subject)
        self.original_exit = original_exit
        self.owners = tuple(owners)
        super().__init__(f'{category}: {subject}')


def need(condition, category, subject=''):
    if not condition:
        raise AssetError(category, subject)


@dataclass(frozen=True)
class Candidate:
    source_sha: str
    source_tree: str
    version: str
    build: str
    arch: str

    def __post_init__(self):
        for value in (self.source_sha, self.source_tree):
            hex_value(value, 40)
        for value in (self.version, self.build):
            need(isinstance(value, str) and len(value) <= 128 and
                 re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value),
                 'CANDIDATE_VERSION_INVALID', value)
        need(self.arch == 'arm64', 'CANDIDATE_ARCH_INVALID', self.arch)


@dataclass(frozen=True)
class Paths:
    repo: Path
    exported_source: Path
    dist: Path
    app: Path
    output: Path
    evidence_dir: Path
    iconset: Path

    def __post_init__(self):
        for value in asdict(self).values():
            absolute(value)
        need(self.exported_source == self.output / 'scratch/source' and
             self.dist == self.exported_source / 'web/dist' and
             self.app == self.output / 'scratch/HappyRanch.app' and
             self.evidence_dir == self.output / 'diagnostics' and
             self.iconset == self.output / 'scratch/AppIcon.iconset',
             'CALLER_PATH_LAYOUT_INVALID')
        need(not self.output.is_relative_to(self.repo) and
             not self.repo.is_relative_to(self.output), 'CALLER_ROOTS_OVERLAP')


@dataclass(frozen=True)
class Expectations:
    context_sha256: str
    toolchains_sha256: str
    iconutil_path: Path
    iconutil_sha256: str
    source_receipt_sha256: str
    dist_receipt_sha256: str
    app_receipt_sha256: str
    icon_receipt_sha256: str
    notices_receipt_sha256: str

    def __post_init__(self):
        absolute(self.iconutil_path)
        for key, value in asdict(self).items():
            if key != 'iconutil_path':
                hex_value(value)


@dataclass(frozen=True)
class ReadDomain:
    cap: int = 16 * MiB
    nonempty: bool = False
    text: bool = False


@dataclass(frozen=True)
class CensusDomain:
    entries: int = 4096
    depth: int = 16
    names: int = 4 * MiB


BINARY = ReadDomain()
NONEMPTY = ReadDomain(nonempty=True)
NOTICE = ReadDomain(256 * KiB, True, True)
OWNER = ReadDomain(128, True, True)
PREFLIGHT = ReadDomain(16 * KiB, True, True)
TOOLCHAINS = ReadDomain(MiB, True, True)
STREAM = ReadDomain(MiB)
_budget = contextvars.ContextVar('asset_read_budget', default=None)


class Budget:
    """One command's fixed-profile attempted work; byte views are read-only."""
    def __init__(self, profile='asset-v1'):
        need(profile in ('asset-v1', 'web-evidence-v1'), 'BUDGET_PROFILE_INVALID')
        self.profile = profile
        self._regular = self._json = self.trees = 0
        self._failure = None
        self._asset = Budget() if profile == 'web-evidence-v1' else None
        self._tee = AssetBudgetView(self) if self._asset is not None else None

    @property
    def regular(self):
        return self._regular

    @property
    def json(self):
        return self._json

    def _charge(self, regular, json_count, asset=False):
        need(type(regular) is int and regular >= 0 and
             type(json_count) is int and json_count >= 0, 'BUDGET_INCREMENT_INVALID')
        self._regular += regular
        self._json += json_count
        lane = self._asset if asset else None
        if lane is not None:
            lane._regular += regular
            lane._json += json_count
        try:
            if self._failure is not None:
                raise self._failure
            need(self.regular <= 512 * MiB, 'AGGREGATE_READ_CAP_EXCEEDED', self.profile)
            if lane is not None:
                need(lane.regular <= 512 * MiB, 'AGGREGATE_READ_CAP_EXCEEDED', 'asset-v1')
            need(self.json <= (192 if self.profile == 'web-evidence-v1' else 32) * MiB,
                 'AGGREGATE_JSON_CAP_EXCEEDED', self.profile)
            if lane is not None:
                need(lane.json <= 32 * MiB, 'AGGREGATE_JSON_CAP_EXCEEDED', 'asset-v1')
        except AssetError as exc:
            self._failure = exc
            raise

    def charge(self, count, *, json_bytes=False):
        self._charge(count, count if json_bytes else 0)

    def charge_json(self, count):
        self._charge(0, count)

    @contextlib.contextmanager
    def asset_lane(self):
        view = self._tee if self._tee is not None else self
        token = _budget.set(view)
        try:
            yield view
        finally:
            _budget.reset(token)


class AssetBudgetView:
    """Persistent tee: diagnostics and census use the same enclosing command."""
    def __init__(self, root):
        self.root = root

    @property
    def profile(self):
        return self.root.profile

    @property
    def regular(self):
        return self.root.regular

    @property
    def json(self):
        return self.root.json

    @property
    def trees(self):
        return self.root.trees

    @trees.setter
    def trees(self, value):
        self.root.trees = value

    def charge(self, count, *, json_bytes=False):
        self.root._charge(count, count if json_bytes else 0, True)

    def charge_json(self, count):
        self.root._charge(0, count, True)

    def asset_lane(self):
        return self.root.asset_lane()


@contextlib.contextmanager
def budget_scope(profile):
    need(profile in ('asset-v1', 'web-evidence-v1'), 'BUDGET_PROFILE_INVALID')
    current = _budget.get()
    if current is not None:
        need(current.profile == profile, 'BUDGET_PROFILE_CONFLICT')
        yield current
        return
    meter = Budget(profile)
    token = _budget.set(meter)
    try:
        yield meter
    finally:
        _budget.reset(token)


def bounded(function):
    @functools.wraps(function)
    def call(*args, **kwargs):
        if _budget.get() is not None:
            return function(*args, **kwargs)
        with budget_scope('asset-v1'):
            return function(*args, **kwargs)
    return call


def hex_value(value, width=64):
    need(isinstance(value, str) and re.fullmatch('[0-9a-f]{' + str(width) + '}', value),
         'DIGEST_INVALID', value)
    return value


def integer(value):
    return type(value) is int and value >= 0


def relative(value):
    need(isinstance(value, str) and value and not value.startswith('/') and
         all(p not in ('', '.', '..') for p in value.split('/')), 'RELATIVE_PATH_INVALID', value)
    try:
        raw = value.encode('utf-8', 'strict')
    except UnicodeError as exc:
        raise AssetError('PATH_UTF8_INVALID', value) from exc
    need(len(raw) <= 1024 and len(value.split('/')) <= 16 and
         all(len(p.encode()) <= 255 for p in value.split('/')) and
         not any(ord(c) < 32 or ord(c) == 127 for c in value), 'PATH_LIMIT_OR_CONTROL', value)
    return value


def absolute(value):
    value = str(value)
    need(value.startswith('/') and value != '/' and len(value.encode()) <= 4096,
         'ABSOLUTE_PATH_REQUIRED', value)
    # The depth16 limit is for descent *within* selected trees, not the
    # deployment's absolute workspace ancestry.
    for component in value[1:].split('/'):
        relative(component)
    return Path(value)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_uid,
            info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def directory(info, name=''):
    return dict(relative=name, dev=info.st_dev, ino=info.st_ino,
                uid=info.st_uid, mode=info.st_mode)


DIRECTORY_CONTEXT = 'DIRECTORY_CONTEXT '


def _directory_path(value: object) -> tuple[str | None, bool]:
    """Bound the serialized path, keeping whole Unicode codepoints/escapes."""
    if value is None:
        return None, False
    value = str(value)
    if len(json.dumps(value, ensure_ascii=True)) <= 192:
        return value, False
    prefix, size = '', 2 + len('...')  # JSON quotes and truncation marker.
    for character in value:
        width = len(json.dumps(character, ensure_ascii=True)) - 2
        if size + width > 192:
            break
        prefix += character
        size += width
    return prefix + '...', True


def _directory_note(context: dict) -> str:
    note = DIRECTORY_CONTEXT + json.dumps(context, ensure_ascii=True, separators=(',', ':'))
    if len(note) > 768 or not note.isascii() or any(ord(c) < 32 for c in note):
        raise ValueError('directory diagnostic cannot be represented')
    return note


def directory_context(error: AssetError, *, info: os.stat_result | None = None,
                      owned: bool | None = None, cursor: object = None,
                      root: object = None, role: str | None = None) -> None:
    """Best-effort traceback evidence only; retain no second/full path copy."""
    try:
        notes = getattr(error, '__notes__', [])
        index = next((i for i, note in enumerate(notes)
                      if note.startswith(DIRECTORY_CONTEXT)), None)
        if info is not None:
            cursor, cursor_truncated = _directory_path(cursor)
            root, root_truncated = _directory_path(root)
            context = dict(category=error.category, mode=oct(info.st_mode), uid=info.st_uid,
                           owned=owned, role=role, cursor_truncated=cursor_truncated,
                           root_truncated=root_truncated, cursor=cursor, root=root)
        elif index is not None:
            context = json.loads(notes[index][len(DIRECTORY_CONTEXT):])
            if context['role'] is not None:
                return
            context['role'] = role
            context['root'], context['root_truncated'] = _directory_path(root)
        else:
            return
        note = _directory_note(context)
        if index is None:
            error.add_note(note)
        else:
            updated = list(notes)
            updated[index] = note
            error.__notes__ = updated
    except Exception:
        # A diagnostic failure must never replace the original guard refusal.
        pass


def check_dir(info: os.stat_result, owned: bool, cursor: object = None,
              root: object = None) -> None:
    try:
        need(stat.S_ISDIR(info.st_mode), 'DIRECTORY_REQUIRED')
        need(info.st_uid == os.getuid() if owned else info.st_uid in (0, os.getuid()),
             'DIRECTORY_OWNER_INVALID')
        need(not info.st_mode & 0o022, 'DIRECTORY_MODE_UNSAFE')
    except AssetError as error:
        directory_context(error, info=info, owned=owned, cursor=cursor, root=root)
        raise


class RootHandle:
    """Pin the complete absolute ancestry and recheck each named edge on use."""
    def __init__(self, path, boundary, require_owned):
        self.path = absolute(path)
        self.boundary = Path(boundary)
        need(self.boundary.is_absolute() and self.path.is_relative_to(self.boundary),
             'ROOT_BOUNDARY_INVALID', self.path)
        self.owned = require_owned
        self.chain = []
        try:
            fd = os.open('/', DIR_FLAGS)
            self.chain.append((fd, None, None, directory(os.fstat(fd))))
            check_dir(os.fstat(fd), False, Path('/'), self.path)
            cursor, boundary_dev = Path('/'), None
            for part in self.path.parts[1:]:
                parent = fd
                fd = os.open(part, DIR_FLAGS, dir_fd=parent)
                info = os.fstat(fd)
                self.chain.append((fd, parent, part, directory(info)))
                cursor = cursor / part
                if boundary_dev is not None:
                    need(info.st_dev == boundary_dev, 'CROSS_DEVICE_DESCENT', self.path)
                if self.boundary != Path('/') and cursor == self.boundary:
                    boundary_dev = info.st_dev
                check_dir(info, False, cursor, self.path)
            self.fd = fd
            check_dir(os.fstat(fd), require_owned, cursor, self.path)
            self.dev = os.fstat(fd).st_dev
            self.verify()
        except BaseException:
            self.close()
            raise

    def verify(self):
        for fd, parent, name, saved in self.chain:
            need(directory(os.fstat(fd)) == saved, 'DIRECTORY_IDENTITY_CHANGED', self.path)
            if parent is not None:
                need(directory(os.stat(name, dir_fd=parent, follow_symlinks=False)) == saved,
                     'DIRECTORY_PATH_REPLACED', self.path)

    def close(self):
        for fd, *_ in reversed(self.chain):
            os.close(fd)
        self.chain = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        try:
            if _[0] is None:
                self.verify()
        finally:
            self.close()


@bounded
def open_root(path: Path, boundary: Path, require_owned: bool) -> RootHandle:
    return RootHandle(path, boundary, require_owned)


@contextlib.contextmanager
def parent_at(root, name, *, create=False):
    relative(name)
    root.verify()
    chain = []
    parent = root.fd
    cursor = root.path
    try:
        for part in name.split('/')[:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
            fd = os.open(part, DIR_FLAGS, dir_fd=parent)
            info = os.fstat(fd)
            chain.append((fd, parent, part, directory(info)))
            cursor = cursor / part
            check_dir(info, root.owned, cursor, root.path)
            need(info.st_dev == root.dev, 'CROSS_DEVICE_DESCENT', name)
            parent = fd
        yield parent, name.split('/')[-1]
        for fd, parent_fd, part, saved in chain:
            need(directory(os.fstat(fd)) == saved and
                 directory(os.stat(part, dir_fd=parent_fd, follow_symlinks=False)) == saved,
                 'DIRECTORY_PATH_REPLACED', name)
        root.verify()
    finally:
        for fd, *_ in reversed(chain):
            os.close(fd)


@contextlib.contextmanager
def subject_fd(root, name, domain):
    with parent_at(root, name) as (parent, leaf):
        fd = os.open(leaf, FLAGS, dir_fd=parent)
        try:
            before = os.fstat(fd)
            need(stat.S_ISREG(before.st_mode), 'REGULAR_FILE_REQUIRED', name)
            need(before.st_nlink == 1, 'FILE_LINK_COUNT_INVALID', name)
            need(before.st_uid == os.getuid() if root.owned else before.st_uid in (0, os.getuid()),
                 'FILE_OWNER_INVALID', name)
            need(not before.st_mode & 0o022, 'FILE_MODE_UNSAFE', name)
            need(before.st_dev == root.dev, 'CROSS_DEVICE_DESCENT', name)
            need(before.st_size <= domain.cap, 'INPUT_CAP_EXCEEDED', name)
            need(not domain.nonempty or before.st_size > 0, 'EMPTY_INPUT', name)
            yield fd, before
            need(identity(os.fstat(fd)) == identity(before) and
                 identity(os.stat(leaf, dir_fd=parent, follow_symlinks=False)) == identity(before),
                 'SUBJECT_UNSTABLE', name)
        finally:
            os.close(fd)


def subject_row(name, info, digest):
    return dict(relative=name, size=info.st_size, sha256=digest, dev=info.st_dev,
                ino=info.st_ino, mode=info.st_mode, uid=info.st_uid, nlink=info.st_nlink,
                mtime_ns=info.st_mtime_ns, ctime_ns=info.st_ctime_ns)


@bounded
def read_subject(root: RootHandle, relative: str, domain: ReadDomain = BINARY):
    return read_bytes(root, relative, domain, collect=False)[0]


@bounded
def read_bytes(root, name, domain, *, collect=True):
    digest = hashlib.sha256()
    data = bytearray() if collect else None
    # Text is buffered only in the explicitly small text domains.
    need(not domain.text or domain.cap <= MiB, 'TEXT_DOMAIN_INVALID', name)
    if domain.text and data is None:
        data = bytearray()
    with subject_fd(root, name, domain) as (fd, info):
        count = 0
        while True:
            chunk = os.read(fd, min(64 * KiB, domain.cap - count + 1))
            if not chunk:
                break
            count += len(chunk)
            _budget.get().charge(len(chunk))
            need(count <= domain.cap, 'INPUT_CAP_EXCEEDED', name)
            digest.update(chunk)
            if data is not None:
                data.extend(chunk)
        need(count == info.st_size, 'SUBJECT_SHORT_OR_GROWING', name)
        if domain.text:
            bytes(data).decode('utf-8', 'strict')
        row = subject_row(name, info, digest.hexdigest())
    return row, bytes(data) if data is not None else None


@bounded
def same_bytes(left, lname, right, rname, domain=BINARY):
    with subject_fd(left, lname, domain) as (lfd, li), subject_fd(right, rname, domain) as (rfd, ri):
        need(li.st_size == ri.st_size, 'COPY_SIZE_MISMATCH', rname)
        count = 0
        while True:
            a, b = os.read(lfd, 64 * KiB), os.read(rfd, 64 * KiB)
            _budget.get().charge(len(a) + len(b))
            count += len(a)
            need(count <= domain.cap, 'INPUT_CAP_EXCEEDED', lname)
            need(a == b, 'COPY_BYTES_MISMATCH', rname)
            if not a:
                break
        need(count == li.st_size, 'SUBJECT_SHORT_OR_GROWING', lname)


@bounded
def create_once(root, name, data, domain=BINARY):
    need(isinstance(data, bytes) and len(data) <= domain.cap and
         (data or not domain.nonempty), 'WRITE_CAP_OR_EMPTY', name)
    if domain.text:
        data.decode('utf-8', 'strict')
    with parent_at(root, name, create=True) as (parent, leaf):
        fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                     0o600, dir_fd=parent)
        try:
            view = memoryview(data)
            while view:
                count = os.write(fd, view[:64 * KiB])
                need(count > 0, 'WRITE_INCOMPLETE', name)
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
    row = read_subject(root, name, domain)
    need(row['sha256'] == hashlib.sha256(data).hexdigest() and row['size'] == len(data),
         'WRITE_READBACK_MISMATCH', name)
    return row


@bounded
def copy_once(source_root, source_relative, target_root, target_relative,
              domain=BINARY, allow_identical_existing=False):
    original = read_subject(source_root, source_relative, domain)
    with parent_at(target_root, target_relative, create=True) as (parent, leaf):
        try:
            fd = os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
        except FileExistsError:
            need(allow_identical_existing, 'DESTINATION_EXISTS', target_relative)
        else:
            try:
                with subject_fd(source_root, source_relative, domain) as (src, info):
                    copied = 0
                    while True:
                        chunk = os.read(src, 64 * KiB)
                        if not chunk:
                            break
                        copied += len(chunk)
                        _budget.get().charge(len(chunk))
                        need(copied <= domain.cap, 'INPUT_CAP_EXCEEDED', source_relative)
                        view = memoryview(chunk)
                        while view:
                            count = os.write(fd, view)
                            need(count > 0, 'WRITE_INCOMPLETE', target_relative)
                            view = view[count:]
                    need(copied == info.st_size, 'SUBJECT_SHORT_OR_GROWING', source_relative)
                os.fsync(fd)
            finally:
                os.close(fd)
    same_bytes(source_root, source_relative, target_root, target_relative, domain)
    target = read_subject(target_root, target_relative, domain)
    need(read_subject(source_root, source_relative, domain) == original,
         'SOURCE_CHANGED_DURING_COPY', source_relative)
    return {'source': original, 'target': target}


@bounded
def census(root, domain=CensusDomain()):
    budget = _budget.get()
    budget.trees += 1
    need(budget.trees <= 3, 'CONCURRENT_CENSUS_CAP_EXCEEDED')
    files, dirs, count, name_bytes = [], [], 0, 0
    aliases = set()

    def walk(fd, prefix, depth):
        nonlocal count, name_bytes
        need(depth <= domain.depth, 'TREE_DEPTH_EXCEEDED', prefix)
        before = directory(os.fstat(fd), prefix)
        check_dir(os.fstat(fd), root.owned, root.path / prefix, root.path)
        need(before['dev'] == root.dev, 'CROSS_DEVICE_DESCENT', prefix)
        dirs.append(before)
        names = []
        with os.scandir(fd) as entries:
            for entry in entries:
                count += 1
                need(count <= domain.entries, 'TREE_ENTRY_CAP_EXCEEDED', prefix)
                name = f'{prefix}/{entry.name}' if prefix else entry.name
                relative(name)
                name_bytes += len(name.encode())
                need(name_bytes <= domain.names, 'TREE_NAMES_CAP_EXCEEDED', name)
                alias = unicodedata.normalize('NFC', name).casefold()
                need(alias not in aliases, 'NAMESPACE_ALIAS', name)
                aliases.add(alias)
                names.append(entry.name)
        saved = {}
        for name in sorted(names):
            rel = f'{prefix}/{name}' if prefix else name
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            saved[name] = identity(info)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, DIR_FLAGS, dir_fd=fd)
                try:
                    walk(child, rel, depth + 1)
                finally:
                    os.close(child)
                need(directory(os.stat(name, dir_fd=fd, follow_symlinks=False), rel) == dirs_by_name[rel],
                     'DIRECTORY_PATH_REPLACED', rel)
            else:
                files.append(read_subject(root, rel))
        # Bounded second enumeration includes hidden names and empty directories.
        after = []
        with os.scandir(fd) as entries:
            for entry in entries:
                need(len(after) < domain.entries, 'TREE_ENTRY_CAP_EXCEEDED', prefix)
                after.append(entry.name)
        need(sorted(after) == sorted(names), 'TREE_MEMBERSHIP_CHANGED', prefix)
        for name in names:
            now = os.stat(name, dir_fd=fd, follow_symlinks=False)
            # Directory times/link counts change only on mutation; census never writes.
            need(identity(now) == saved[name], 'TREE_SUBJECT_CHANGED', name)
        need(directory(os.fstat(fd), prefix) == before, 'DIRECTORY_IDENTITY_CHANGED', prefix)
        dirs_by_name[prefix] = before

    dirs_by_name = {}
    try:
        root.verify()
        walk(root.fd, '', 0)
        root.verify()
        return {'root': directory(os.fstat(root.fd)), 'directories': sorted(dirs, key=lambda r: r['relative']),
                'files': sorted(files, key=lambda r: r['relative']), 'file_count': len(files),
                'directory_count': len(dirs), 'census_complete': True}
    finally:
        budget.trees -= 1


def encoded(value):
    return (json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                       allow_nan=False) + '\n').encode('utf-8')


def hash_json(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def pairs_hook(items):
    result = {}
    for key, value in items:
        need(key not in result, 'JSON_DUPLICATE_KEY', key)
        result[key] = value
    return result


class FrozenObject(dict):
    """Read-only local JSON result; JSON encoding/equality keep their meaning."""
    def _refuse(self, *_args, **_kwargs):
        raise TypeError('asset observation is immutable')

    __setitem__ = __delitem__ = clear = pop = popitem = setdefault = update = __ior__ = _refuse


class FrozenArray(list):
    def _refuse(self, *_args, **_kwargs):
        raise TypeError('asset observation is immutable')

    __setitem__ = __delitem__ = append = clear = extend = insert = pop = remove = reverse = sort = __iadd__ = __imul__ = _refuse


def frozen(value):
    if isinstance(value, dict):
        return FrozenObject((k, frozen(v)) for k, v in value.items())
    if isinstance(value, list):
        return FrozenArray(frozen(v) for v in value)
    return value


def parse_json(raw):
    # Bound nesting/nodes before json.loads, respecting quoted/escaped content.
    text = raw.decode('utf-8', 'strict')
    depth, nodes, quoted, escape, scalar = 0, 0, False, False, False
    for c in text:
        if quoted:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == '"':
                quoted = False
        elif c == '"':
            quoted = True
            scalar = False
            nodes += 1
        elif c in '[{':
            scalar = False
            depth += 1
            nodes += 1
            need(depth <= 16, 'JSON_DEPTH_CAP_EXCEEDED')
        elif c in ']}':
            scalar = False
            depth -= 1
        elif c in ' \t\r\n,:':
            scalar = False
        elif not scalar:
            scalar = True
            nodes += 1
        need(nodes <= 100000, 'JSON_NODE_CAP_EXCEEDED')
    value = json.loads(text, object_pairs_hook=pairs_hook,
                       parse_constant=lambda v: (_ for _ in ()).throw(AssetError('JSON_NONFINITE', v)))
    count = 0

    def visit(v, level=0):
        nonlocal count
        count += 1
        need(count <= 100000 and level <= 16, 'JSON_COMPLEXITY_CAP_EXCEEDED')
        if isinstance(v, str):
            need(len(v.encode('utf-8')) <= 4096, 'JSON_STRING_CAP_EXCEEDED')
        elif isinstance(v, dict):
            for k, child in v.items():
                visit(k, level + 1)
                visit(child, level + 1)
        elif isinstance(v, list):
            for child in v:
                visit(child, level + 1)
        else:
            need(v is None or type(v) in (bool, int), 'JSON_SCALAR_INVALID')
    visit(value)
    return value


@bounded
def read_closed_json(root, name, byte_limit, shape):
    row, raw = read_bytes(root, name, ReadDomain(byte_limit, True))
    _budget.get().charge_json(len(raw))
    value = parse_json(raw)
    shape(value)
    return frozen(value), frozen(row)


def keys(value, expected, site):
    need(isinstance(value, dict) and set(value) == set(expected), 'RECORD_KEYS_INVALID', site)


def subject_shape(value):
    keys(value, SUBJECT_KEYS, 'subject')
    relative(value['relative'])
    hex_value(value['sha256'])
    need(all(integer(v) for k, v in value.items() if k not in ('relative', 'sha256')),
         'SUBJECT_TYPE_INVALID', value['relative'])
    need(stat.S_ISREG(value['mode']) and value['nlink'] == 1, 'SUBJECT_SHAPE_INVALID', value['relative'])


def dir_shape(value):
    keys(value, DIR_KEYS, 'directory')
    if value['relative']:
        relative(value['relative'])
    need(all(integer(value[k]) for k in DIR_KEYS - {'relative'}) and stat.S_ISDIR(value['mode']),
         'DIRECTORY_SHAPE_INVALID')


def list_of(value, validator, *, count=None):
    need(isinstance(value, list) and (count is None or len(value) == count), 'RECORD_CARDINALITY_INVALID')
    for row in value:
        validator(row)


def unique_rows(rows, field):
    values = [r[field] for r in rows]
    need(len(values) == len(set(values)), 'RECORD_DUPLICATE_ROW', field)


def census_shape(value):
    keys(value, ('root', 'directories', 'files', 'file_count', 'directory_count', 'census_complete'), 'census')
    dir_shape(value['root'])
    list_of(value['directories'], dir_shape)
    list_of(value['files'], subject_shape)
    unique_rows(value['directories'], 'relative')
    unique_rows(value['files'], 'relative')
    need(value['census_complete'] is True and integer(value['file_count']) and
         integer(value['directory_count']) and value['file_count'] == len(value['files']) and
         value['directory_count'] == len(value['directories']) and
         value['file_count'] + value['directory_count'] - 1 <= 4096,
         'CENSUS_COUNTS_INVALID')


def reference_shape(value):
    keys(value, ('record_type', 'filename', 'size', 'sha256', 'run_id'), 'token')
    need(value['record_type'] in FILES and value['filename'] == FILES[value['record_type']] and
         integer(value['size']) and 0 < value['size'] <= CAPS[value['record_type']], 'TOKEN_INVALID')
    hex_value(value['sha256'])
    run_value(value['run_id'])


def run_value(value):
    need(isinstance(value, str) and str(uuid.UUID(value)) == value, 'RUN_ID_INVALID', value)


def pair_shape(value):
    keys(value, ('source_root', 'target_root', 'source', 'target', 'rights_relation'), 'pair')
    need(value['rights_relation'] == 'UNRESOLVED', 'COPY_RIGHTS_RELATION_INVALID')
    need(value['source_root'] in ('public', 'dist', 'source', 'generated') and
         value['target_root'] in ('dist', 'app', 'retained', 'iconset'), 'PAIR_ROOT_INVALID')
    subject_shape(value['source'])
    subject_shape(value['target'])
    need((value['source']['size'], value['source']['sha256']) ==
         (value['target']['size'], value['target']['sha256']), 'PAIR_CONTENT_INVALID')


def component_shape(value):
    keys(value, ('component', 'disposition', 'scope', 'notices'), 'font-component')
    need(value['component'] in {r[0] for r in FONTS} and value['disposition'] == 'RETAINED_TEXT' and
         value['scope'] == 'exact original font/public copy relation', 'FONT_COMPONENT_INVALID')
    list_of(value['notices'], lambda r: keys(r, ('source', 'destination', 'sha256'), 'notice-component'), count=1)
    n = value['notices'][0]
    need(n['destination'] in {f'{NOTICE_BASE}/{r[3]}' for r in FONTS} and isinstance(n['source'], str),
         'FONT_NOTICE_DESTINATION_INVALID')
    hex_value(n['sha256'])


def icon_inputs_shape(value):
    keys(value, ('originals', 'copies', 'census'), 'icon-inputs')
    list_of(value['originals'], subject_shape, count=7)
    unique_rows(value['originals'], 'relative')
    list_of(value['copies'], pair_shape, count=10)
    need({r['source']['relative'] for r in value['copies']} ==
         {f'{ICON_BASE}/icon_{n}.png' for n in PNGS} and
         {r['target']['relative'] for r in value['copies']} == {v for _, v in ICON_MAP},
         'ICON_MAPPING_INVALID')
    originals = {row['relative']: row for row in value['originals']}
    for size, (length, digest) in PNGS.items():
        name = f'{ICON_BASE}/icon_{size}.png'
        need(name in originals and (originals[name]['size'], originals[name]['sha256']) == (length, digest),
             'ICON_ORIGINAL_TUPLE_INVALID', name)
    for row, (size, target) in zip(value['copies'], ICON_MAP, strict=True):
        need(row['source_root'] == 'source' and row['target_root'] == 'iconset' and
             row['source'] == originals[f'{ICON_BASE}/icon_{size}.png'] and
             row['target']['relative'] == target, 'ICON_MAPPING_INVALID', target)
    census_shape(value['census'])
    need(len(value['census']['files']) == 10 and len(value['census']['directories']) == 1,
         'ICON_CENSUS_INVALID')


def payload_shape(kind, value):
    if kind == 'context':
        keys(value, ('paths', 'directories', 'owner', 'preflight', 'iconutil_path', 'tool', 'toolchains',
                     'initial_source_sha256', 'source_file_count', 'source_directory_count'), kind)
        keys(value['paths'], Paths.__dataclass_fields__, 'paths')
        for path in value['paths'].values():
            absolute(path)
        keys(value['directories'], ('repo', 'exported_source', 'output', 'output_parent', 'evidence_dir', 'scratch'), 'roots')
        for row in value['directories'].values():
            dir_shape(row)
        for name in ('owner', 'preflight', 'tool', 'toolchains'):
            subject_shape(value[name])
        absolute(value['iconutil_path'])
        hex_value(value['initial_source_sha256'])
        need(integer(value['source_file_count']) and integer(value['source_directory_count']), 'CONTEXT_COUNTS_INVALID')
    elif kind == 'source':
        keys(value, ('census', 'git'), kind)
        census_shape(value['census'])
        list_of(value['git'], lambda row: keys(row, ('relative', 'mode', 'blob', 'subject'), 'git'))
        unique_rows(value['git'], 'relative')
        for row in value['git']:
            relative(row['relative'])
            hex_value(row['blob'], 40)
            subject_shape(row['subject'])
            need(row['mode'] in ('100644', '100755'), 'GIT_MODE_INVALID')
    elif kind in ('dist', 'app'):
        fields = ('census', 'upstream', 'copies', 'generated_or_other_unattributed') if kind == 'dist' else (
            'census', 'upstream', 'copies', 'removed_by_existing_policy')
        keys(value, fields, kind)
        census_shape(value['census'])
        reference_shape(value['upstream'])
        need(value['upstream']['record_type'] == ('source' if kind == 'dist' else 'dist'), 'UPSTREAM_KIND_INVALID')
        list_of(value['copies'], pair_shape)
        unique_rows([r['target'] for r in value['copies']], 'relative')
        if kind == 'dist':
            list_of(value['generated_or_other_unattributed'], lambda r: keys(r, ('relative', 'rights_relation'), 'unattributed'))
            for row in value['generated_or_other_unattributed']:
                relative(row['relative'])
                need(row['rights_relation'] == 'UNRESOLVED', 'RIGHTS_RELATION_INVALID')
        else:
            list_of(value['removed_by_existing_policy'], subject_shape)
            unique_rows(value['removed_by_existing_policy'], 'relative')
    elif kind == 'notices':
        keys(value, ('upstream', 'manifest', 'fonts', 'retained'), kind)
        reference_shape(value['upstream'])
        need(value['upstream']['record_type'] == 'app', 'UPSTREAM_KIND_INVALID')
        subject_shape(value['manifest'])
        list_of(value['fonts'], lambda r: keys(r, ('font', 'notice', 'provenance', 'component'), 'font'), count=3)
        for row in value['fonts']:
            list_of(row['font'], subject_shape, count=3)
            list_of(row['notice'], subject_shape, count=4)
            keys(row['provenance'], ('task', 'manifest_sha256', 'font'), 'provenance')
            need(row['provenance']['task'] == 'TASK-11585' and row['provenance']['manifest_sha256'] == MANIFEST_SHA and
                 row['provenance']['font'] in {r[0] for r in FONTS}, 'FONT_PROVENANCE_INVALID')
            component_shape(row['component'])
        unique_rows([r['component'] for r in value['fonts']], 'component')
        list_of(value['retained'], subject_shape, count=2)
        unique_rows(value['retained'], 'relative')
        for row, (font, size, sha, notice, nsize, nsha) in zip(value['fonts'], FONTS, strict=True):
            need(row['component']['component'] == font and row['provenance']['font'] == font and
                 row['component']['notices'][0]['destination'] == f'{NOTICE_BASE}/{notice}' and
                 row['component']['notices'][0]['sha256'] == nsha, 'FONT_OWNER_RELATION_INVALID', font)
            for subject, name in zip(row['font'], ('web/public/fonts/' + font, 'fonts/' + font,
                                                  APP_WEB + '/fonts/' + font), strict=True):
                need((subject['relative'], subject['size'], subject['sha256']) == (name, size, sha),
                     'FONT_SUBJECT_RELATION_INVALID', font)
            for subject, name in zip(row['notice'], ('web/public/fonts/' + notice, 'fonts/' + notice,
                                                    APP_WEB + '/fonts/' + notice, NOTICE_BASE + '/' + notice), strict=True):
                need((subject['relative'], subject['size'], subject['sha256']) == (name, nsize, nsha),
                     'FONT_NOTICE_RELATION_INVALID', font)
        need({r['relative'] for r in value['retained']} == {NOTICE_BASE + '/' + r[3] for r in FONTS},
             'FONT_RETAINED_SET_INVALID')
    elif kind == 'icon':
        keys(value, ('upstream', 'inputs', 'tool_before', 'tool_after', 'toolchains', 'argv', 'cwd',
                     'process', 'stdout', 'stderr', 'generated_to_app', 'generated_root', 'app_root',
                     'invocation_context'), kind)
        dir_shape(value['generated_root'])
        dir_shape(value['app_root'])
        keys(value['upstream'], ('context_sha256', 'initial_source_sha256'), 'icon-upstream')
        for digest in value['upstream'].values():
            hex_value(digest)
        icon_inputs_shape(value['inputs'])
        for name in ('tool_before', 'tool_after', 'toolchains', 'stdout', 'stderr'):
            subject_shape(value[name])
        need(value['tool_before'] == value['tool_after'], 'ICON_TOOL_CHANGED')
        proc = value['process']
        keys(proc, ('pid', 'start_ns', 'end_ns', 'wall_start_ns', 'wall_end_ns', 'exit', 'signal',
                    'quiescent', 'stdout_complete', 'stderr_complete', 'timed_out', 'overflow',
                    'stdout_bytes_observed', 'stderr_bytes_observed', 'stdout_bytes_retained', 'stderr_bytes_retained',
                    'stdout_omitted_observed', 'stderr_omitted_observed', 'unread_bytes_unknown'), 'process')
        need(all(integer(proc[n]) for n in ('pid', 'start_ns', 'end_ns', 'wall_start_ns', 'wall_end_ns', 'exit', 'signal')) and
             proc['pid'] > 0 and proc['exit'] == 0 and proc['signal'] == 0 and proc['end_ns'] >= proc['start_ns'] and
             all(proc[n] is True for n in ('quiescent', 'stdout_complete', 'stderr_complete')) and
             proc['timed_out'] is False and proc['overflow'] is False and proc['unread_bytes_unknown'] is False,
             'ICON_PROCESS_INCOMPLETE')
        for stream in ('stdout', 'stderr'):
            need(all(integer(proc[stream + suffix]) for suffix in ('_bytes_observed', '_bytes_retained', '_omitted_observed')) and
                 proc[stream + '_bytes_observed'] == proc[stream + '_bytes_retained'] == value[stream]['size'] and
                 proc[stream + '_omitted_observed'] == 0, 'ICON_STREAM_COUNTS_INVALID', stream)
        invocation = value['invocation_context']
        keys(invocation, ('system', 'machine', 'environment'), 'icon-context')
        need(invocation['system'] == 'Darwin' and invocation['machine'] == 'arm64', 'ICON_PLATFORM_INVALID')
        keys(invocation['environment'], ('MACOSX_DEPLOYMENT_TARGET', 'DEVELOPER_DIR', 'SDKROOT'), 'icon-environment')
        need(all(v is None or isinstance(v, str) for v in invocation['environment'].values()), 'ICON_ENVIRONMENT_INVALID')
        need(isinstance(value['argv'], list) and len(value['argv']) == 6 and
             all(type(v) is str for v in value['argv']), 'ICON_ARGV_INVALID')
        need(value['argv'][1:4] == ['-c', 'icns', '-o'], 'ICON_ARGV_INVALID')
        for index in (0, 4, 5):
            absolute(value['argv'][index])
        absolute(value['cwd'])
        pair_shape(value['generated_to_app'])
        need(value['stdout']['relative'] == 'iconutil.stdout' and value['stderr']['relative'] == 'iconutil.stderr' and
             value['generated_to_app']['source_root'] == 'generated' and
             value['generated_to_app']['target_root'] == 'app' and
             value['generated_to_app']['source']['relative'] == 'AppIcon.icns' and
             value['generated_to_app']['target']['relative'] == 'Contents/Resources/AppIcon.icns',
             'ICON_OUTPUT_REFERENCE_INVALID')


def record_shape(kind, value):
    fields = {'encoding', 'record_type', 'candidate', 'run_id', 'observed_start_ns', 'observed_end_ns', 'payload'}
    if kind != 'context':
        fields.add('context_sha256')
    keys(value, fields, kind)
    need(value['encoding'] == ENCODING and value['record_type'] == kind, 'RECORD_ENCODING_INVALID', kind)
    keys(value['candidate'], Candidate.__dataclass_fields__, 'candidate')
    Candidate(**value['candidate'])
    run_value(value['run_id'])
    need(integer(value['observed_start_ns']) and integer(value['observed_end_ns']) and
         value['observed_end_ns'] >= value['observed_start_ns'], 'RECORD_TIME_INVALID', kind)
    if kind != 'context':
        hex_value(value['context_sha256'])
    payload_shape(kind, value['payload'])


def root_for(path, owned=True, *, boundary=Path('/')):
    return open_root(path, boundary, owned)


def absent(root, name):
    with parent_at(root, name) as (fd, leaf):
        try:
            os.stat(leaf, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        raise AssetError('DESTINATION_EXISTS', name)


def mkdir_once(root, name):
    with parent_at(root, name) as (fd, leaf):
        os.mkdir(leaf, 0o700, dir_fd=fd)


def paths_dict(paths):
    return {k: str(v) for k, v in asdict(paths).items()}


@bounded
def bindings(candidate, paths, run_id):
    run_value(run_id)
    dirs = {}
    for label, path in (('repo', paths.repo), ('exported_source', paths.exported_source),
                        ('output', paths.output), ('output_parent', paths.output.parent),
                        ('evidence_dir', paths.evidence_dir), ('scratch', paths.output / 'scratch')):
        with root_for(path) as root:
            dirs[label] = directory(os.fstat(root.fd))
            if label in ('output', 'output_parent', 'evidence_dir', 'scratch'):
                need(os.fstat(root.fd).st_mode & 0o077 == 0, 'PRIVATE_ROOT_REQUIRED', path)
    with root_for(paths.output) as root:
        owner, raw = read_bytes(root, 'owner', OWNER)
        need(raw == (run_id + '\n').encode('ascii'), 'OWNER_RUN_MISMATCH', 'owner')
        preflight, pre_subject = read_closed_json(root, 'preflight.json', PREFLIGHT.cap,
            lambda v: keys(v, (*Candidate.__dataclass_fields__, 'output', 'preflight'), 'preflight'))
        need(preflight == {**asdict(candidate), 'output': str(paths.output), 'preflight': 'VALIDATED_NO_BUILD_EFFECTS'},
             'PREFLIGHT_CALLER_MISMATCH', 'preflight.json')
    return dirs, owner, pre_subject


def write_record(kind, candidate, paths, run_id, context, payload, start):
    value = dict(encoding=ENCODING, record_type=kind, candidate=asdict(candidate), run_id=run_id,
                 observed_start_ns=start, observed_end_ns=time.monotonic_ns(), payload=payload)
    if kind != 'context':
        value['context_sha256'] = context
    record_shape(kind, value)
    data = encoded(value)
    need(len(data) <= CAPS[kind], 'RECEIPT_CAP_EXCEEDED', kind)
    # Validate the serialized limits too, before creating the final one-shot file.
    parse_json(data)
    with root_for(paths.evidence_dir) as root:
        row = create_once(root, FILES[kind], data, ReadDomain(CAPS[kind], True))
    return dict(record_type=kind, filename=FILES[kind], size=row['size'], sha256=row['sha256'], run_id=run_id)


@contextlib.contextmanager
def stage(kind, candidate, paths, run_id):
    """Failed stages occupy their fixed filename forever; no resume/overwrite."""
    with root_for(paths.evidence_dir) as root:
        absent(root, FILES[kind])
    try:
        yield time.monotonic_ns()
    except BaseException as exc:
        # Keep original tool status even if persistence itself fails. No success
        # token is issued for this deliberately non-completed encoding.
        if kind == 'notices' and isinstance(exc, AssetError) and not exc.owners:
            # Early source/copy refusals also durably withhold their owners.
            # Retain the actual early category/site; don't call it a reached
            # downstream retention assertion.
            exc.owners = tuple(r[0] for r in FONTS[1:] if 'Newsreader' in str(exc)) or tuple(r[0] for r in FONTS)
        try:
            with root_for(paths.evidence_dir) as root:
                failure = {
                    'encoding': ENCODING, 'record_type': kind, 'state': 'INCOMPLETE',
                    'candidate': asdict(candidate), 'run_id': run_id,
                    'error': getattr(exc, 'category', type(exc).__name__),
                    'subject': str(getattr(exc, 'subject', ''))[:1024],
                    'original_exit': getattr(exc, 'original_exit', None),
                    'refused_owners': list(getattr(exc, 'owners', ())),
                }
                if isinstance(exc, IconFailure):
                    failure.update(process=exc.process, argv=exc.argv,
                                   inspection_error=exc.inspection_error,
                                   inputs=exc.inputs, tool_before=exc.tool_before,
                                   operation_context=exc.operation_context)
                create_once(root, FILES[kind], encoded(failure), ReadDomain(CAPS[kind], True))
        except (OSError, ValueError) as persistence:
            print(f'ASSET_FAILURE_PERSISTENCE_FAILED: {type(persistence).__name__}', file=sys.stderr)
        raise


def load_record(kind, candidate, paths, run_id, digest, context=None):
    hex_value(digest)
    with root_for(paths.evidence_dir) as root:
        # Digest refusal deliberately precedes parser checks. Parser fixtures
        # must explicitly bind their malformed bytes to reach parsing.
        subject = read_subject(root, FILES[kind], ReadDomain(CAPS[kind], True))
        need(subject['sha256'] == digest, 'EXPECTED_RECEIPT_DIGEST_MISMATCH', FILES[kind])
        value, second = read_closed_json(root, FILES[kind], CAPS[kind], lambda v: record_shape(kind, v))
        need(second == subject, 'RECEIPT_CHANGED', FILES[kind])
    need(value['candidate'] == asdict(candidate) and value['run_id'] == run_id,
         'RECORD_CALLER_MISMATCH', kind)
    if kind != 'context':
        need(value['context_sha256'] == context, 'RECORD_CONTEXT_MISMATCH', kind)
    token = dict(record_type=kind, filename=FILES[kind], size=subject['size'], sha256=digest, run_id=run_id)
    return value['payload'], token


def check_context(candidate, paths, run_id, context_digest):
    payload, _ = load_record('context', candidate, paths, run_id, context_digest)
    dirs, owner, preflight = bindings(candidate, paths, run_id)
    need(payload['paths'] == paths_dict(paths) and payload['directories'] == dirs and
         payload['owner'] == owner and payload['preflight'] == preflight,
         'CONTEXT_LIVE_BINDING_MISMATCH')
    return payload


def captured(command, cwd, cap, stderr_cap=MiB):
    """Bounded Git transport, with the same owned-process reaping primitive."""
    result = process_capture(command, cwd, cap, stderr_cap, git_transport=True)
    need(result['process']['exit'] == 0 and result['process']['quiescent'] and
         not result['process']['timed_out'] and not result['process']['overflow'],
         'GIT_PROCESS_FAILED', command[1])
    _budget.get().charge(len(result['stdout']) + len(result['stderr']))
    return result['stdout']


def git(paths, *args, cap=8 * MiB):
    # Read immutable Git objects only; no candidate executable/config hooks.
    return captured(['git', '--no-replace-objects', '-C', str(paths.repo), *args], paths.repo, cap)


def source_payload(candidate, paths):
    with root_for(paths.repo):
        need(git(paths, 'rev-parse', candidate.source_sha + '^{tree}', cap=128).decode().strip() == candidate.source_tree,
             'GIT_TREE_CALLER_MISMATCH')
    with root_for(paths.exported_source / 'web/public', boundary=paths.exported_source) as root:
        current = census(root)
        by_name = {r['relative']: r for r in current['files']}
        for row in current['files']:
            name = row['relative']
            need(Path(name).name not in RETIRED, 'RETIRED_FONT_PRESENT', name)
        for row in [*current['files'], *current['directories']]:
            name = row['relative']
            alias = unicodedata.normalize('NFC', name).casefold()
            need(alias != 'index.html' and alias.split('/')[0] != 'assets', 'RESERVED_PUBLIC_NAMESPACE', name)
        tree = git(paths, 'ls-tree', '-rz', candidate.source_sha, '--', 'web/public')
        need(not tree or tree.endswith(b'\0'), 'GIT_TREE_TRUNCATED')
        rows = []
        names = set()
        for raw in tree.split(b'\0')[:-1]:
            need(len(rows) < 4096, 'GIT_TREE_ENTRY_CAP_EXCEEDED')
            meta, raw_name = raw.split(b'\t', 1)
            mode, kind, blob = meta.decode('ascii').split(' ')
            name = raw_name.decode('utf-8', 'strict')
            need(name.startswith('web/public/') and kind == 'blob' and mode in ('100644', '100755'),
                 'GIT_PUBLIC_MODE_INVALID', name)
            name = relative(name[len('web/public/'):])
            need(name not in names, 'GIT_DUPLICATE_PATH', name)
            names.add(name)
            need(name in by_name, 'PUBLIC_EXPORT_MISSING', name)
            actual = by_name[name]
            need(bool(actual['mode'] & 0o111) == (mode == '100755'), 'GIT_EXPORT_MODE_MISMATCH', name)
            git_blob_compare(paths, root, name, blob, actual)
            rows.append(dict(relative=name, mode=mode, blob=blob, subject=actual))
        need(names == set(by_name), 'PUBLIC_EXPORT_EXTRA', sorted(set(by_name) - names))
        # Git cannot contain empty directories; reject unselected export dirs.
        expected_dirs = {''}
        for name in names:
            expected_dirs.update(str(p) for p in Path(name).parents if str(p) != '.')
        need({r['relative'] for r in current['directories']} == expected_dirs,
             'PUBLIC_EXPORT_DIRECTORY_MISMATCH')
        need(census(root) == current, 'PUBLIC_SOURCE_CHANGED')
    return dict(census=current, git=sorted(rows, key=lambda r: r['relative']))


def tool_subject(path):
    path = absolute(path)
    with root_for(path.parent, False) as root:
        row = read_subject(root, path.name, NONEMPTY)
        need(row['mode'] & 0o111, 'ICON_TOOL_NOT_EXECUTABLE', path)
        return row


@bounded
def begin_assets(candidate, paths, expected_run_id, iconutil_path, toolchains_path):
    with stage('context', candidate, paths, expected_run_id) as start:
        dirs, owner, preflight = bindings(candidate, paths, expected_run_id)
        need(toolchains_path == paths.evidence_dir / 'toolchains.txt', 'TOOLCHAINS_PATH_INVALID')
        with root_for(paths.output) as root:
            for name in ('scratch/source/web/dist', 'scratch/HappyRanch.app', 'scratch/AppIcon.iconset', 'scratch/icon-output'):
                absent(root, name)
        with root_for(paths.evidence_dir) as root:
            for name in FILES.values():
                absent(root, name)
            tc = read_subject(root, 'toolchains.txt', TOOLCHAINS)
        src = source_payload(candidate, paths)
        tool = tool_subject(iconutil_path)
        payload = dict(paths=paths_dict(paths), directories=dirs, owner=owner, preflight=preflight,
                       iconutil_path=str(iconutil_path), tool=tool, toolchains=tc,
                       initial_source_sha256=hash_json(src), source_file_count=src['census']['file_count'],
                       source_directory_count=src['census']['directory_count'])
        ctx = write_record('context', candidate, paths, expected_run_id, None, payload, start)
    with stage('source', candidate, paths, expected_run_id) as start:
        source = write_record('source', candidate, paths, expected_run_id, ctx['sha256'], src, start)
    return {'context': ctx, 'source': source, 'iconutil_path': str(iconutil_path),
            'iconutil_sha256': tool['sha256'], 'toolchains_sha256': tc['sha256']}


def checked_source(candidate, paths, run_id, context, digest):
    ctx = check_context(candidate, paths, run_id, context)
    old, token = load_record('source', candidate, paths, run_id, digest, context)
    need(hash_json(old) == ctx['initial_source_sha256'] and
         old['census']['file_count'] == ctx['source_file_count'] and
         old['census']['directory_count'] == ctx['source_directory_count'], 'SOURCE_CONTEXT_MISMATCH')
    need(source_payload(candidate, paths) == old, 'SOURCE_OBSERVATION_CHANGED')
    return old, token


@bounded
def check_public_before_build(candidate, paths, expected_run_id, expected_context_sha256,
                              expected_source_receipt_sha256):
    need(not os.environ.get('I18N_BROWSER_EVIDENCE') and not os.environ.get('I18N_W2A_EVIDENCE'),
         'INSTRUMENTED_WEB_BUILD_REFUSED')
    checked_source(candidate, paths, expected_run_id, expected_context_sha256, expected_source_receipt_sha256)
    with root_for(paths.exported_source) as root:
        absent(root, 'web/dist')


def pair(source_root, target_root, source, target):
    return dict(source_root=source_root, target_root=target_root, source=source, target=target,
                rights_relation='UNRESOLVED')


def measured_dist(paths, source, upstream):
    with root_for(paths.dist) as dist, root_for(paths.exported_source / 'web/public', boundary=paths.exported_source) as public:
        measured = census(dist)
        by_name = {r['relative']: r for r in measured['files']}
        copies = []
        for original in source['census']['files']:
            name = original['relative']
            need(name in by_name, 'DIST_PUBLIC_COPY_MISSING', name)
            same_bytes(public, name, dist, name)
            need(read_subject(public, name) == original, 'PUBLIC_SOURCE_CHANGED', name)
            copies.append(pair('public', 'dist', original, by_name[name]))
        need(not any(Path(n).name in RETIRED for n in by_name), 'RETIRED_FONT_PRESENT')
        original_names = {r['relative'] for r in source['census']['files']}
        generated = [dict(relative=n, rights_relation='UNRESOLVED') for n in sorted(set(by_name) - original_names)]
        need(census(dist) == measured, 'DIST_CHANGED_DURING_OBSERVATION')
    return dict(census=measured, upstream=upstream, copies=copies, generated_or_other_unattributed=generated)


@bounded
def observe_dist(candidate, paths, expected_run_id, expected_context_sha256, expected_source_receipt_sha256):
    with stage('dist', candidate, paths, expected_run_id) as start:
        source, token = checked_source(candidate, paths, expected_run_id, expected_context_sha256,
                                       expected_source_receipt_sha256)
        payload = measured_dist(paths, source, token)
        return write_record('dist', candidate, paths, expected_run_id, expected_context_sha256, payload, start)


@bounded
def check_dist_before_copy(candidate, paths, expected_run_id, expected_context_sha256, expected_dist_receipt_sha256):
    # An existing seam immediately before cp, not a new observation or token.
    check_context(candidate, paths, expected_run_id, expected_context_sha256)
    old, token = load_record('dist', candidate, paths, expected_run_id, expected_dist_receipt_sha256, expected_context_sha256)
    source, _ = checked_source(candidate, paths, expected_run_id, expected_context_sha256, old['upstream']['sha256'])
    need(measured_dist(paths, source, old['upstream']) == old, 'DIST_OBSERVATION_CHANGED')
    return old, token


def measured_app(paths, dist, upstream):
    with root_for(paths.dist) as left, root_for(paths.app / APP_WEB, boundary=paths.app) as right:
        need(census(left) == dist['census'], 'DIST_OBSERVATION_CHANGED')
        current = census(right)
        sources = {r['relative']: r for r in dist['census']['files']}
        targets = {r['relative']: r for r in current['files']}
        removed = [v for n, v in sources.items() if Path(n).name.endswith('.map')]
        kept = {n for n in sources if not Path(n).name.endswith('.map')}
        need(set(targets) == kept, 'APP_COPY_SET_MISMATCH',
             dict(missing=sorted(kept - set(targets)), extra=sorted(set(targets) - kept)))
        need({r['relative'] for r in current['directories']} ==
             {r['relative'] for r in dist['census']['directories']}, 'APP_DIRECTORY_SET_MISMATCH')
        copies = []
        for name in sorted(kept):
            same_bytes(left, name, right, name)
            copies.append(pair('dist', 'app', sources[name], targets[name]))
        need(census(right) == current, 'APP_CHANGED_DURING_OBSERVATION')
    return dict(census=current, upstream=upstream, copies=copies,
                removed_by_existing_policy=sorted(removed, key=lambda r: r['relative']))


@bounded
def observe_app_copy(candidate, paths, expected_run_id, expected_context_sha256, expected_dist_receipt_sha256):
    with stage('app', candidate, paths, expected_run_id) as start:
        dist, token = check_dist_before_copy(candidate, paths, expected_run_id, expected_context_sha256,
                                             expected_dist_receipt_sha256)
        payload = measured_app(paths, dist, token)
        return write_record('app', candidate, paths, expected_run_id, expected_context_sha256, payload, start)


def original_manifest(candidate, paths):
    name = 'packaging/notice-data/font-assets.json'
    with root_for(paths.exported_source) as source:
        subject, raw = read_bytes(source, name, NOTICE)
        verify_git_subject(candidate, paths, source, name, subject)
    need(subject['size'] == 5592 and subject['sha256'] == MANIFEST_SHA, 'FONT_MANIFEST_PIN_MISMATCH', name)
    value = parse_json(raw)
    keys(value, ('purpose', 'acquisition', 'files'), 'font-manifest')
    keys(value['acquisition'], ('task', 'source_commit'), 'font-acquisition')
    need(value['acquisition'] == {'task': 'TASK-11585', 'source_commit': '3828be6170f79e49378298d9f79a5198a3e59ac2'},
         'FONT_ACQUISITION_INVALID')
    list_of(value['files'], lambda r: keys(r, ('family', 'destination', 'size', 'sha256', 'git_blob_sha1',
                                             'source', 'notice', 'google_fonts_import'), 'font-fact'), count=3)
    unique_rows(value['files'], 'destination')
    facts = {r['destination']: r for r in value['files']}
    for name, size, sha, notice, nsize, nsha in FONTS:
        row = facts.get('web/public/fonts/' + name)
        need(row is not None and row['size'] == size and row['sha256'] == sha and
             row['notice']['destination'] == 'web/public/fonts/' + notice and
             row['notice']['size'] == nsize and row['notice']['sha256'] == nsha,
             'FONT_FACT_TUPLE_INVALID', name)
        keys(row['source'], ('repository', 'commit', 'tree', 'path', 'url'), 'font-source')
        keys(row['notice'], ('destination', 'size', 'sha256', 'git_blob_sha1', 'source_path', 'source_url',
                             'source_commit', 'source_tree', 'relationship'), 'font-notice')
        keys(row['google_fonts_import'], ('commit', 'tree', 'relationship', 'version_note'), 'font-import')
    return subject


def font_payload(candidate, paths, upstream, *, retain):
    manifest = original_manifest(candidate, paths)
    fonts, retained = [], {}
    with root_for(paths.exported_source) as source, root_for(paths.dist) as dist, root_for(paths.app) as app:
        # Open only fixed paths through pinned descriptors. Validate every source,
        # copy and BOTH existing targets before writing either notice.
        originals = {}
        for font, size, sha, notice, nsize, nsha in FONTS:
            owners = (font,) if notice.startswith('Hanken') else tuple(r[0] for r in FONTS[1:])
            try:
                for name, expected_size, expected_sha, domain in ((font, size, sha, NONEMPTY), (notice, nsize, nsha, NOTICE)):
                    src = 'web/public/fonts/' + name
                    drel = 'fonts/' + name
                    arel = APP_WEB + '/' + drel
                    srow = read_subject(source, src, domain)
                    need((srow['size'], srow['sha256']) == (expected_size, expected_sha), 'FONT_ORIGINAL_PIN_MISMATCH', src)
                    drow, arow = read_subject(dist, drel, domain), read_subject(app, arel, domain)
                    same_bytes(source, src, dist, drel, domain)
                    same_bytes(source, src, app, arel, domain)
                    originals[name] = (srow, drow, arow)
                target = NOTICE_BASE + '/' + notice
                # Missing fixed parent is safely created, without opening any
                # record-selected path and without touching either notice file.
                with parent_at(app, target, create=retain) as (parent, leaf):
                    try:
                        os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        need(retain, 'RETAINED_NOTICE_MISSING', target)
                    else:
                        same_bytes(source, 'web/public/fonts/' + notice, app, target, NOTICE)
            except (OSError, ValueError) as exc:
                raise AssetError('FONT_OWNER_REFUSED', f'{font}: {exc}', owners=owners) from exc
        for _, _, _, notice, _, _ in FONTS:
            if notice in retained:
                continue
            src, target = 'web/public/fonts/' + notice, NOTICE_BASE + '/' + notice
            if retain:
                copy_once(source, src, app, target, NOTICE, allow_identical_existing=True)
            same_bytes(source, src, app, target, NOTICE)
            retained[notice] = read_subject(app, target, NOTICE)
        for font, _, _, notice, _, nsha in FONTS:
            component = dict(component=font, disposition='RETAINED_TEXT',
                             scope='exact original font/public copy relation', notices=[{
                                 'source': str(paths.exported_source / 'web/public/fonts' / notice),
                                 'destination': NOTICE_BASE + '/' + notice, 'sha256': nsha}])
            fonts.append(dict(font=list(originals[font]), notice=[*originals[notice], retained[notice]],
                              provenance=dict(task='TASK-11585', manifest_sha256=MANIFEST_SHA, font=font),
                              component=component))
        # All source/dist/app originals are reopened after retention, including
        # the shared Newsreader relation; later good reads never clear a refusal.
        for name, previous in originals.items():
            domain = NOTICE if name.endswith('.txt') else NONEMPTY
            now = (read_subject(source, 'web/public/fonts/' + name, domain),
                   read_subject(dist, 'fonts/' + name, domain),
                   read_subject(app, APP_WEB + '/fonts/' + name, domain))
            need(now == previous, 'FONT_CHANGED_DURING_RETENTION', name)
    return dict(upstream=upstream, manifest=manifest, fonts=fonts,
                retained=sorted(retained.values(), key=lambda r: r['relative']))


@bounded
def retain_font_notices(candidate, paths, expected_run_id, expected_context_sha256,
                        expected_app_receipt_sha256):
    with stage('notices', candidate, paths, expected_run_id) as start:
        check_context(candidate, paths, expected_run_id, expected_context_sha256)
        app, token = load_record('app', candidate, paths, expected_run_id, expected_app_receipt_sha256, expected_context_sha256)
        dist, dtoken = check_dist_before_copy(candidate, paths, expected_run_id, expected_context_sha256, app['upstream']['sha256'])
        need(measured_app(paths, dist, dtoken) == app, 'APP_OBSERVATION_CHANGED')
        payload = font_payload(candidate, paths, token, retain=True)
        receipt = write_record('notices', candidate, paths, expected_run_id, expected_context_sha256, payload, start)
        return [row['component'] for row in payload['fonts']], receipt


@bounded
def icon_inputs(candidate, paths):
    originals, copies = [], []
    with root_for(paths.exported_source) as source, root_for(paths.iconset) as iconset:
        for size, (length, digest) in PNGS.items():
            name = f'{ICON_BASE}/icon_{size}.png'
            row = read_subject(source, name, NONEMPTY)
            need((row['size'], row['sha256']) == (length, digest), 'ICON_ORIGINAL_PIN_MISMATCH', name)
            verify_git_subject(candidate, paths, source, name, row)
            originals.append(row)
        by_name = {r['relative']: r for r in originals}
        for size, target in ICON_MAP:
            name = f'{ICON_BASE}/icon_{size}.png'
            same_bytes(source, name, iconset, target, NONEMPTY)
            copies.append(pair('source', 'iconset', by_name[name], read_subject(iconset, target, NONEMPTY)))
        current = census(iconset)
        need({r['relative'] for r in current['files']} == {target for _, target in ICON_MAP} and
             len(current['directories']) == 1, 'ICONSET_MEMBERSHIP_MISMATCH')
    result = dict(originals=sorted(originals, key=lambda r: r['relative']), copies=copies, census=current)
    icon_inputs_shape(result)
    return result


@bounded
def prepare_iconset(candidate, paths, expected_run_id, expected_context_sha256):
    check_context(candidate, paths, expected_run_id, expected_context_sha256)
    with root_for(paths.exported_source) as source:
        # Validate all seven originals before creating any iconset destination.
        for size, (length, digest) in PNGS.items():
            name = f'{ICON_BASE}/icon_{size}.png'
            row = read_subject(source, name, NONEMPTY)
            need((row['size'], row['sha256']) == (length, digest), 'ICON_ORIGINAL_PIN_MISMATCH', name)
            verify_git_subject(candidate, paths, source, name, row)
        with root_for(paths.output / 'scratch', boundary=paths.output) as scratch:
            mkdir_once(scratch, 'AppIcon.iconset')
        with root_for(paths.iconset) as iconset:
            for size, target in ICON_MAP:
                copy_once(source, f'{ICON_BASE}/icon_{size}.png', iconset, target, NONEMPTY)
    return icon_inputs(candidate, paths)


@contextlib.contextmanager
def process_signals(failures):
    """Defer catchable interruption until capture owns the launched handle.

    Never block signals across Popen: the child keeps its normal signal mask.
    The first failure wins, including during cleanup; repeated signals cannot
    unwind registration, reaping or closure. Synchronous CLI/main-thread only.
    """
    saved = {}

    def interrupted(signum, _frame):
        if not failures:
            failures.append(AssetError('PROCESS_INTERRUPTED', str(signum)))

    try:
        for signum in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
            # Save before installing; even a partial setup is restored below.
            saved[signum] = signal.getsignal(signum)
            signal.signal(signum, interrupted)
        yield
    except BaseException as exc:
        if not failures:
            failures.append(exc)
        raise failures[0]
    finally:
        # Restore every saved handler even after partial setup or an error.
        # Owned process resources are already cleaned before normal restoration.
        for signum, handler in saved.items():
            try:
                signal.signal(signum, handler)
            except BaseException as exc:
                if not failures:
                    failures.append(exc)


def process_capture(argv, cwd, stdout_cap, stderr_cap, *, streams=None, stdout_consumer=None, git_transport=False):
    """One real process group, bounded pipes/deadline, TERM/KILL and wait.

    Streams are fixed descriptor-owned files in native use. No injected runner,
    platform override, shell, retry or second transform. Pipe EOF and reaping are
    separate observations; a descendant holding a pipe cannot cause an unbounded
    drain. Missing quiescence never supplies a successful receipt.
    """
    start, wall = time.monotonic_ns(), time.time_ns()
    proc = selector = None
    owned_fds = []
    code = None
    group_gone = False
    output = [bytearray(), bytearray()]
    counts, retained, eof = [0, 0], [0, 0], [False, False]
    caps = (stdout_cap, stderr_cap)
    timed_out = overflow = killed = False
    deadline = start / 1_000_000_000 + 60
    failures = []

    def checkpoint():
        if failures:
            raise failures[0]

    def close_fd(fd):
        # Never retry a close with an uncertain result or skip later resources.
        owned_fds.remove(fd)
        os.close(fd)

    def terminate(sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    with process_signals(failures):
        try:
            env = dict(os.environ)
            if git_transport:
                env = {k: os.environ[k] for k in ('PATH', 'HOME', 'TMPDIR', 'SYSTEMROOT') if k in os.environ}
                env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_NO_REPLACE_OBJECTS='1', GIT_TERMINAL_PROMPT='0')
            # Retain even partially initialized resources before fallible init.
            selector = selectors.DefaultSelector.__new__(selectors.DefaultSelector)
            selector.__init__()
            checkpoint()
            # Own transport endpoints before Popen, including partial setup.
            out_read, out_write = os.pipe()
            owned_fds.extend((out_read, out_write))
            err_read, err_write = os.pipe()
            owned_fds.extend((err_read, err_write))
            null_fd = os.open(os.devnull, os.O_RDONLY)
            owned_fds.append(null_fd)
            checkpoint()
            # Popen does not guarantee child reaping on every post-fork init
            # error. Own the instance before init so pid/pipes remain reachable.
            # Recording handlers do not unwind the fork-to-pid handoff, and no
            # blocked signal mask is inherited by the child. Exactly one init.
            proc = subprocess.Popen.__new__(subprocess.Popen)
            checkpoint()
            proc.__init__(argv, cwd=cwd, env=env, stdin=null_fd,
                          stdout=out_write, stderr=err_write, start_new_session=True)
            checkpoint()
            for fd in (out_write, err_write, null_fd):
                close_fd(fd)
            for index, fd in enumerate((out_read, err_read)):
                os.set_blocking(fd, False)
                selector.register(fd, selectors.EVENT_READ, index)
                checkpoint()
            while selector.get_map() or proc.poll() is None:
                checkpoint()
                now = time.monotonic()
                if now >= deadline:
                    if killed:
                        break
                    timed_out = True
                if (timed_out or overflow) and not killed:
                    terminate(signal.SIGTERM)
                    # One short grace interval, then kill the entire owned group.
                    grace = time.monotonic() + 0.25
                    while proc.poll() is None and time.monotonic() < grace:
                        checkpoint()
                        time.sleep(0.01)
                    terminate(signal.SIGKILL)
                    killed = True
                    deadline = time.monotonic() + 1
                for key, _ in selector.select(min(0.1, max(0, deadline - time.monotonic()))):
                    checkpoint()
                    index = key.data
                    chunk = os.read(key.fd, 64 * KiB)
                    if not chunk:
                        eof[index] = True
                        selector.unregister(key.fileobj)
                        continue
                    remaining = max(0, caps[index] - counts[index])
                    kept = chunk[:remaining]
                    counts[index] += len(chunk)
                    if streams:
                        view = memoryview(kept)
                        while view:
                            checkpoint()
                            written = os.write(streams[index], view)
                            need(written > 0, 'ICON_STREAM_WRITE_FAILED')
                            retained[index] += written
                            view = view[written:]
                    elif index == 0 and stdout_consumer is not None:
                        stdout_consumer(kept)
                        retained[index] += len(kept)
                    else:
                        output[index].extend(kept)
                        retained[index] += len(kept)
                    overflow |= counts[index] > caps[index]
            checkpoint()
        except BaseException as exc:
            if not failures:
                failures.append(exc)
        finally:
            # Each acquired resource gets its own attempt, even if an earlier
            # cleanup fails. No checkpoints here: signals keep the first failure
            # and cannot abort cleanup. Reaping has a finite independent bound.
            pid = getattr(proc, 'pid', None)
            if pid is not None:
                try:
                    if failures or proc.poll() is None:
                        terminate(signal.SIGKILL)
                except BaseException as exc:
                    if not failures:
                        failures.append(exc)
                try:
                    code = proc.wait(timeout=1)
                except BaseException as exc:
                    code = proc.returncode  # Actual status if known; else None.
                    if not failures:
                        failures.append(exc)
                try:
                    # EOF alone is not quiescence. Kill surviving descendants,
                    # but never label an observed live/unknown group as gone.
                    os.killpg(proc.pid, 0)
                except ProcessLookupError:
                    group_gone = True
                except BaseException as exc:
                    if not failures:
                        failures.append(exc)
                else:
                    try:
                        terminate(signal.SIGKILL)
                    except BaseException as exc:
                        if not failures:
                            failures.append(exc)
            # These endpoints are ours even if Popen init never returned.
            for fd in tuple(owned_fds):
                try:
                    close_fd(fd)
                except BaseException as exc:
                    if not failures:
                        failures.append(exc)
            if selector is not None:
                try:
                    selector.close()
                except BaseException as exc:
                    if not failures:
                        failures.append(exc)
        if pid is None:
            # No child identity was acquired; retain the original setup error.
            raise failures[0]
        observed = dict(pid=pid, start_ns=start, end_ns=time.monotonic_ns(),
                        wall_start_ns=wall, wall_end_ns=time.time_ns(), exit=code,
                        signal=-code if code is not None and code < 0 else 0,
                        quiescent=not failures and code is not None and group_gone and all(eof),
                        stdout_complete=eof[0] and counts[0] <= caps[0],
                        stderr_complete=eof[1] and counts[1] <= caps[1],
                        timed_out=timed_out, overflow=overflow, unread_bytes_unknown=not all(eof))
        for index, name in enumerate(('stdout', 'stderr')):
            observed.update({name + '_bytes_observed': counts[index], name + '_bytes_retained': retained[index],
                             name + '_omitted_observed': counts[index] - retained[index]})
    if failures:
        observed['quiescent'] = False
        raise IconFailure('PROCESS_CAPTURE_FAILED', observed, argv, original_exit=code,
                          inspection_error=str(failures[0])) from failures[0]
    return dict(process=observed, stdout=bytes(output[0]), stderr=bytes(output[1]))


def verify_git_subject(candidate, paths, root, name, subject):
    raw = git(paths, 'ls-tree', '-z', candidate.source_sha, '--', name)
    need(raw.endswith(b'\0') and raw.count(b'\0') == 1, 'GIT_SUBJECT_MISSING', name)
    meta, path = raw[:-1].split(b'\t', 1)
    mode, kind, blob = meta.decode('ascii').split(' ')
    need(path.decode('utf-8') == name and kind == 'blob' and mode in ('100644', '100755'),
         'GIT_SUBJECT_MODE_INVALID', name)
    git_blob_compare(paths, root, name, blob, subject)
    return blob


def git_blob_compare(paths, root, name, blob, subject):
    size = int(git(paths, 'cat-file', '-s', blob, cap=128).decode('ascii').strip())
    need(0 <= size <= 16 * MiB and size == subject['size'], 'GIT_BLOB_SIZE_INVALID', name)
    sha1 = hashlib.sha1(b'blob ' + str(size).encode() + b'\0')
    sha256 = hashlib.sha256()
    count = 0
    with subject_fd(root, name, BINARY) as (fd, info):
        def consume(chunk):
            nonlocal count
            # A pipe read and a regular-file read may have different chunking.
            data = bytearray()
            while len(data) < len(chunk):
                part = os.read(fd, len(chunk) - len(data))
                if not part:
                    break
                data.extend(part)
            _budget.get().charge(len(data) + len(chunk))
            need(bytes(data) == chunk, 'PUBLIC_EXPORT_BYTES_MISMATCH', name)
            count += len(chunk)
            sha1.update(chunk)
            sha256.update(chunk)
        result = process_capture(['git', '--no-replace-objects', '-C', str(paths.repo), 'cat-file', 'blob', blob],
                                 paths.repo, 16 * MiB, MiB, stdout_consumer=consume, git_transport=True)
        p = result['process']
        need(p['exit'] == 0 and p['quiescent'] and not p['timed_out'] and not p['overflow'],
             'GIT_BLOB_PROCESS_FAILED', name)
        extra = os.read(fd, 1)
        _budget.get().charge(len(extra) + len(result['stderr']))
        need(not extra and count == size == info.st_size and sha1.hexdigest() == blob and
             sha256.hexdigest() == subject['sha256'], 'GIT_BLOB_IDENTITY_INVALID', name)


@bounded
def run_icon_operation(candidate, paths, expected_run_id, expected_context_sha256,
                       expected_iconutil_path, expected_iconutil_sha256):
    with stage('icon', candidate, paths, expected_run_id) as start:
        ctx = check_context(candidate, paths, expected_run_id, expected_context_sha256)
        need(platform.system() == 'Darwin' and platform.machine() == 'arm64', 'NATIVE_ICON_VENUE_UNAVAILABLE')
        need(str(expected_iconutil_path) == ctx['iconutil_path'] and expected_iconutil_sha256 == ctx['tool']['sha256'],
             'ICON_TOOL_CALLER_MISMATCH')
        tool_before = tool_subject(expected_iconutil_path)
        need(tool_before == ctx['tool'], 'ICON_TOOL_OBSERVATION_CHANGED')
        with root_for(paths.evidence_dir) as evidence:
            tc = read_subject(evidence, 'toolchains.txt', TOOLCHAINS)
            need(tc == ctx['toolchains'], 'TOOLCHAINS_CHANGED')
        # Source expectation was captured by begin, not rediscovered here: its
        # original payload is independently bound by context before use.
        inputs = prepare_iconset(candidate, paths, expected_run_id, expected_context_sha256)
        with root_for(paths.output / 'scratch', boundary=paths.output) as scratch:
            mkdir_once(scratch, 'icon-output')
        generated_path = paths.output / 'scratch/icon-output'
        argv = [str(expected_iconutil_path), '-c', 'icns', '-o', str(generated_path / 'AppIcon.icns'), str(paths.iconset)]
        invocation_context = dict(system=platform.system(), machine=platform.machine(),
                                  environment={k: os.environ.get(k) for k in ('MACOSX_DEPLOYMENT_TARGET', 'DEVELOPER_DIR', 'SDKROOT')})
        parse_json(encoded(invocation_context))
        with root_for(generated_path, boundary=paths.output) as generated, root_for(paths.app) as app, root_for(paths.evidence_dir) as evidence, root_for(paths.exported_source) as source_root:
            absent(generated, 'AppIcon.icns')
            absent(app, 'Contents/Resources/AppIcon.icns')
            need(icon_inputs(candidate, paths) == inputs, 'ICON_INPUTS_CHANGED')
            need(directory(os.fstat(source_root.fd)) == ctx['directories']['exported_source'], 'ICON_CWD_CHANGED')
            operation_context = dict(cwd=str(paths.exported_source), run_id=expected_run_id,
                                     context_sha256=expected_context_sha256, invocation_context=invocation_context,
                                     generated_root=directory(os.fstat(generated.fd)),
                                     app_root=directory(os.fstat(app.fd)), toolchains=tc)
            streams = []
            captured_result = None
            try:
                for name in ('iconutil.stdout', 'iconutil.stderr'):
                    with parent_at(evidence, name) as (parent, leaf):
                        streams.append(os.open(leaf, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                                               0o600, dir_fd=parent))
                try:
                    need(tool_subject(expected_iconutil_path) == tool_before, 'ICON_TOOL_CHANGED')
                    generated.verify()
                    source_root.verify()
                    absent(generated, 'AppIcon.icns')
                    absent(app, 'Contents/Resources/AppIcon.icns')
                    captured_result = process_capture(argv, paths.exported_source, MiB, MiB, streams=streams)
                except IconFailure as exc:
                    exc.inputs, exc.tool_before = inputs, tool_before
                    exc.operation_context = operation_context
                    raise
                try:
                    for fd in streams:
                        os.fsync(fd)
                except OSError as exc:
                    raise IconFailure('ICON_STREAM_PERSISTENCE_FAILED', captured_result['process'], argv,
                                      original_exit=captured_result['process']['exit'], inspection_error=str(exc),
                                      inputs=inputs, tool_before=tool_before, operation_context=operation_context) from exc
            finally:
                close_error = None
                for fd in streams:
                    try:
                        os.close(fd)
                    except OSError as exc:
                        close_error = exc
                if close_error is not None and sys.exc_info()[0] is None:
                    if captured_result is not None:
                        raise IconFailure('ICON_STREAM_CLOSE_FAILED', captured_result['process'], argv,
                                          original_exit=captured_result['process']['exit'],
                                          inspection_error=str(close_error), inputs=inputs,
                                          tool_before=tool_before, operation_context=operation_context) from close_error
                    raise close_error
            proc = captured_result['process']
            # Persist the complete actual operation metadata even on tool error;
            # failed record isn't a completed-success envelope or token.
            tool_exit = proc['exit']
            if tool_exit != 0 or not proc['quiescent'] or proc['timed_out'] or proc['overflow']:
                raise IconFailure('ICON_OPERATION_INCOMPLETE', proc, argv, original_exit=tool_exit,
                                  inputs=inputs, tool_before=tool_before, operation_context=operation_context)
            try:
                need(tool_subject(expected_iconutil_path) == tool_before, 'ICON_TOOL_CHANGED')
                need(icon_inputs(candidate, paths) == inputs, 'ICON_INPUTS_CHANGED')
                need(read_subject(evidence, 'toolchains.txt', TOOLCHAINS) == tc, 'TOOLCHAINS_CHANGED')
                generated.verify()
                output_copy = copy_once(generated, 'AppIcon.icns', app, 'Contents/Resources/AppIcon.icns', NONEMPTY)
                need(census(generated)['file_count'] == 1, 'ICON_OUTPUT_SET_MISMATCH')
                payload = dict(upstream={'context_sha256': expected_context_sha256,
                                         'initial_source_sha256': ctx['initial_source_sha256']}, inputs=inputs,
                               tool_before=tool_before, tool_after=tool_subject(expected_iconutil_path), toolchains=tc,
                               argv=argv, cwd=str(paths.exported_source), process=proc,
                               generated_root=directory(os.fstat(generated.fd)), app_root=directory(os.fstat(app.fd)),
                               invocation_context=invocation_context,
                               stdout=read_subject(evidence, 'iconutil.stdout', STREAM),
                               stderr=read_subject(evidence, 'iconutil.stderr', STREAM),
                               generated_to_app=pair('generated', 'app', output_copy['source'], output_copy['target']))
                return write_record('icon', candidate, paths, expected_run_id, expected_context_sha256, payload, start)
            except (OSError, ValueError) as exc:
                raise IconFailure('ICON_POSTCONDITION_FAILED', proc, argv, original_exit=tool_exit,
                                  inspection_error=str(exc), inputs=inputs, tool_before=tool_before,
                                  operation_context=operation_context) from exc


class IconFailure(AssetError):
    def __init__(self, category, process, argv, *, original_exit, inspection_error='', inputs=None, tool_before=None,
                 operation_context=None):
        self.process = process
        self.inputs = inputs
        self.tool_before = tool_before
        self.operation_context = operation_context
        self.argv = argv
        self.inspection_error = inspection_error
        super().__init__(category, inspection_error, original_exit=original_exit)


@bounded
def verify_assets(candidate, paths, expected_run_id, expected):
    # No partial reader: all independent caller expectations are mandatory and
    # the full actual native-operation prefix must exist. Reopen, don't rebuild.
    ctx = check_context(candidate, paths, expected_run_id, expected.context_sha256)
    need(ctx['iconutil_path'] == str(expected.iconutil_path) and
         ctx['tool']['sha256'] == expected.iconutil_sha256 and
         ctx['toolchains']['sha256'] == expected.toolchains_sha256, 'EXPECTED_TOOL_BINDING_MISMATCH')
    need(tool_subject(expected.iconutil_path) == ctx['tool'], 'ICON_TOOL_OBSERVATION_CHANGED')
    with root_for(paths.evidence_dir) as evidence:
        need(read_subject(evidence, 'toolchains.txt', TOOLCHAINS) == ctx['toolchains'], 'TOOLCHAINS_CHANGED')
    source, stoken = checked_source(candidate, paths, expected_run_id, expected.context_sha256, expected.source_receipt_sha256)
    dist, dtoken = load_record('dist', candidate, paths, expected_run_id, expected.dist_receipt_sha256, expected.context_sha256)
    need(measured_dist(paths, source, stoken) == dist, 'DIST_OBSERVATION_CHANGED')
    app, atoken = load_record('app', candidate, paths, expected_run_id, expected.app_receipt_sha256, expected.context_sha256)
    need(measured_app(paths, dist, dtoken) == app, 'APP_OBSERVATION_CHANGED')
    icon, _ = load_record('icon', candidate, paths, expected_run_id, expected.icon_receipt_sha256, expected.context_sha256)
    need(icon['upstream'] == {'context_sha256': expected.context_sha256,
                             'initial_source_sha256': ctx['initial_source_sha256']} and
         icon_inputs(candidate, paths) == icon['inputs'], 'ICON_INPUTS_OR_CONTEXT_MISMATCH')
    need(icon['tool_before'] == ctx['tool'] == icon['tool_after'] and icon['toolchains'] == ctx['toolchains'],
         'ICON_TOOL_RECORD_MISMATCH')
    generated_path = paths.output / 'scratch/icon-output'
    need(icon['argv'] == [str(expected.iconutil_path), '-c', 'icns', '-o', str(generated_path / 'AppIcon.icns'), str(paths.iconset)] and
         icon['cwd'] == str(paths.exported_source), 'ICON_INVOCATION_MISMATCH')
    with root_for(generated_path, boundary=paths.output) as generated, root_for(paths.app) as app_root, root_for(paths.evidence_dir) as evidence:
        need(directory(os.fstat(generated.fd)) == icon['generated_root'] and
             directory(os.fstat(app_root.fd)) == icon['app_root'], 'ICON_OUTPUT_ROOT_CHANGED')
        same_bytes(generated, 'AppIcon.icns', app_root, 'Contents/Resources/AppIcon.icns', NONEMPTY)
        need(icon['generated_to_app'] == pair('generated', 'app', read_subject(generated, 'AppIcon.icns', NONEMPTY),
                                             read_subject(app_root, 'Contents/Resources/AppIcon.icns', NONEMPTY)),
             'ICON_OUTPUT_OBSERVATION_CHANGED')
        need(census(generated)['file_count'] == 1 and census(generated)['directory_count'] == 1, 'ICON_OUTPUT_SET_MISMATCH')
        for name in ('stdout', 'stderr'):
            need(read_subject(evidence, 'iconutil.' + name, STREAM) == icon[name], 'ICON_STREAM_OBSERVATION_CHANGED', name)
    notices, _ = load_record('notices', candidate, paths, expected_run_id, expected.notices_receipt_sha256, expected.context_sha256)
    need(font_payload(candidate, paths, atoken, retain=False) == notices, 'FONT_NOTICE_OBSERVATION_CHANGED')
    # Recheck context/caller subjects at the end; this cannot detect an
    # undetectable change-and-restore or authenticate a hostile custodian.
    check_context(candidate, paths, expected_run_id, expected.context_sha256)
    return dict(public_copy_relations='MEASURED', font_notice_relations='RETAINED',
                icon_operation_record='CONSISTENT_LOCAL_RECORD')


class Once(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        if getattr(namespace, self.dest, None) is not None:
            parser.error('ASSET_ARGUMENT_DUPLICATE: ' + option_string)
        setattr(namespace, self.dest, values)


def add_arguments(parser, *, expectations=False):
    for key in (*Candidate.__dataclass_fields__, 'run_id', 'source', 'exported_source', 'dist', 'app',
                'output_dir', 'evidence_dir', 'iconset'):
        parser.add_argument('--' + key.replace('_', '-'), required=True, action=Once)
    if expectations:
        for key in ('asset_context_sha256', 'toolchains_sha256', 'iconutil_path', 'iconutil_sha256',
                    'asset_source_sha256', 'asset_dist_sha256', 'asset_app_sha256',
                    'asset_icon_sha256', 'asset_notices_sha256'):
            parser.add_argument('--' + key.replace('_', '-'), required=True, action=Once)


def invocation(args):
    candidate = Candidate(**{k: getattr(args, k) for k in Candidate.__dataclass_fields__})
    paths = Paths(*(absolute(getattr(args, key)) for key in ('source', 'exported_source', 'dist', 'app',
                                                           'output_dir', 'evidence_dir', 'iconset')))
    run_value(args.run_id)
    return candidate, paths


def expectations(args):
    return Expectations(args.asset_context_sha256, args.toolchains_sha256, absolute(args.iconutil_path),
                        args.iconutil_sha256, args.asset_source_sha256, args.asset_dist_sha256,
                        args.asset_app_sha256, args.asset_icon_sha256, args.asset_notices_sha256)


def verify_arguments(args):
    candidate, paths = invocation(args)
    return verify_assets(candidate, paths, args.run_id, expectations(args))


def print_token(value):
    data = encoded(value)
    need(len(data) <= 4 * KiB, 'TOKEN_CAP_EXCEEDED')
    print(data.decode('utf-8'), end='')


def token_field(raw, field):
    need(len(raw.encode()) <= 4 * KiB, 'TOKEN_CAP_EXCEEDED')
    value = parse_json(raw.encode())
    if field in ('context', 'source', 'iconutil_path', 'iconutil_sha256', 'toolchains_sha256'):
        keys(value, ('context', 'source', 'iconutil_path', 'iconutil_sha256', 'toolchains_sha256'), 'start-token')
        reference_shape(value['context'])
        reference_shape(value['source'])
        absolute(value['iconutil_path'])
        hex_value(value['iconutil_sha256'])
        hex_value(value['toolchains_sha256'])
        need(value['context']['run_id'] == value['source']['run_id'] and
             value['context']['record_type'] == 'context' and value['source']['record_type'] == 'source', 'START_TOKEN_INVALID')
        return value[field]['sha256'] if field in ('context', 'source') else value[field]
    need(field == 'sha256', 'TOKEN_FIELD_INVALID')
    reference_shape(value)
    return value['sha256']


def main():
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    subs = parser.add_subparsers(dest='command', required=True)
    token = subs.add_parser('token-field', allow_abbrev=False)
    token.add_argument('--token', action=Once, required=True)
    token.add_argument('--field', action=Once, required=True)
    for command in ('begin', 'check-public', 'observe-dist', 'check-dist', 'observe-app', 'icon', 'verify'):
        q = subs.add_parser(command, allow_abbrev=False)
        add_arguments(q, expectations=command == 'verify')
        if command == 'begin':
            q.add_argument('--iconutil-path', action=Once, required=True)
            q.add_argument('--toolchains-path', action=Once, required=True)
        elif command != 'verify':
            q.add_argument('--asset-context-sha256', action=Once, required=True)
            if command in ('check-public', 'observe-dist'):
                q.add_argument('--asset-source-sha256', action=Once, required=True)
            elif command in ('check-dist', 'observe-app'):
                q.add_argument('--asset-dist-sha256', action=Once, required=True)
            elif command == 'icon':
                q.add_argument('--iconutil-path', action=Once, required=True)
                q.add_argument('--iconutil-sha256', action=Once, required=True)
    args = parser.parse_args()
    if args.command == 'token-field':
        print(token_field(args.token, args.field))
        return
    candidate, paths = invocation(args)
    if args.command == 'begin':
        result = begin_assets(candidate, paths, args.run_id, absolute(args.iconutil_path), absolute(args.toolchains_path))
    elif args.command == 'verify':
        result = verify_arguments(args)
    else:
        common = (candidate, paths, args.run_id, args.asset_context_sha256)
        if args.command == 'icon':
            result = run_icon_operation(*common, absolute(args.iconutil_path), args.iconutil_sha256)
        elif args.command in ('check-public', 'observe-dist'):
            function = check_public_before_build if args.command == 'check-public' else observe_dist
            result = function(*common, args.asset_source_sha256)
        else:
            function = check_dist_before_copy if args.command == 'check-dist' else observe_app_copy
            result = function(*common, args.asset_dist_sha256)
        if args.command in ('check-public', 'check-dist'):
            return
    print_token(result)


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        original = getattr(error, 'original_exit', None)
        raise SystemExit(original if type(original) is int and original > 0 else
                         128 - original if type(original) is int and original < 0 else 1)
