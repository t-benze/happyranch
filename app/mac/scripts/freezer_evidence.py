#!/usr/bin/env python3
"""RETIRED / NONSHIPPING under Founder THR104 seq369.

Historical implementation only; Mac build and all shipping readers do not import
or invoke this file. Its former protocol is not a current acceptance gate.

Private freezer custody records. Code payloads are opaque; closure stays false.

This observer never runs a tool, compiler, archive payload or native transform.
Caller tokens are required at the reader, and scratch generations must survive.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import csv
import hashlib
import importlib.metadata
import importlib.util
import io
import json
import mmap
import os
import platform
import runpy
import shutil
import stat
import struct
import sys
import sysconfig
import time
import unicodedata
import zipfile
import zlib
from dataclasses import asdict
from pathlib import Path

ASSET = runpy.run_path(str(Path(__file__).with_name('asset_evidence.py')))
MiB = 1024**2
RECORD_CAP = 8 * MiB
AGGREGATE_RECORD_CAP = 32 * MiB
JSON_IO_CAP = 256 * MiB
CONTAINER_CAP = 512 * MiB
MAX_MEMBERS = 16384
KINDS = {'PYMODULE', 'PYMODULE-1', 'PYMODULE-2', 'PYSOURCE', 'PYSOURCE-1', 'PYSOURCE-2',
         'BINARY', 'EXTENSION', 'DATA', 'ZIPFILE', 'PYZ', 'EXECUTABLE', 'SYMLINK', 'OPTION'}
VENDOR = 'packaging/vendor/pyinstaller-6.21.0'
WHEEL = 'pyinstaller-6.21.0-1happyranchcustody5-py3-none-macosx_10_13_universal2.whl'
ENCODING = 'happyranch-freezer@1'
FILES = {k: f'freezer-{k}.json' for k in ('context', 'inputs', 'output', 'copy')}
SUBJECT_KEYS = {'root', 'relative', 'identity', 'size', 'sha256', 'snapshot'}

PAYLOAD_FIELDS = {
    'context': {'paths','directories','owner','preflight','asset_context','toolchains','sources','wheel','provenance','contract'},
    'inputs': {'toolchain','installed','roots','analysis','lanes','contributors','collect_arguments','attribution','aliases','unresolved'},
    'output': {'collect_fragment','collect_census','archives','inputs','contributor_edges','unresolved'},
    'copy': {'output','before','after','mode_changes','unresolved'},
}


class FreezerError(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise FreezerError('FREEZER_' + code)


def keys(value, expected, code='CLOSED_SCHEMA'):
    need(type(value) is dict and set(value) == set(expected), code)


def integer(n):
    return type(n) is int and n >= 0


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def _record_profile(kind=None):
    # Only the already-known operation kind selects capacity, before file IO.
    need(kind is None or kind in FILES, 'RECORD_KIND')
    if kind in ('inputs', 'output'):
        return AGGREGATE_RECORD_CAP, 1000000
    return (65536 if kind == 'context' else RECORD_CAP), 100000


def _json_object(value, node_cap):
    count = 0
    boolean_fields = {'declared_strip','declared_upx','availability','effective_strip','effective_upx',
                      'strict_arch_validation','append_pkg','ready'}

    def visit(item, level=0, field=None):
        nonlocal count
        count += 1
        need(count <= node_cap and level <= 16, 'JSON_COMPLEXITY_CAP_EXCEEDED')
        if isinstance(item, str):
            need(len(item.encode('utf-8', 'strict')) <= 4096, 'JSON_STRING_CAP_EXCEEDED')
        elif isinstance(item, dict):
            for key, child in item.items():
                need(isinstance(key, str), 'JSON_KEY_INVALID')
                visit(key, level + 1)
                visit(child, level + 1, key)
        elif isinstance(item, (list, tuple)):
            # JSONEncoder represents producer tuples as arrays, as before.
            for child in item:
                visit(child, level + 1)
        else:
            need(item is None or type(item) in (bool, int), 'JSON_SCALAR_INVALID')
            if type(item) is bool:
                need(field in boolean_fields, 'JSON_BOOLEAN_FIELD')
    visit(value)


def encoded(value, *, _kind=None):
    cap, nodes = _record_profile(_kind)
    # Record producers use the matching strict object admission. Other callers
    # include vendor fragment byte comparisons: preserve their existing semantic
    # validators rather than imposing the default parser's 100k fence on an
    # already materialized vendor fragment (vendor object cap is 1m).
    if _kind is not None:
        _json_object(value, nodes)
    # For records, preflight bounds strings/nodes/depth before chunk allocation.
    result = bytearray()
    encoder = json.JSONEncoder(sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False)
    for part in encoder.iterencode(value):
        chunk = part.encode('utf-8', 'strict')
        need(len(result) + len(chunk) + 1 <= cap, 'JSON_CAP')
        result.extend(chunk)
    need(len(result) + 1 <= cap, 'JSON_CAP')
    result.extend(b'\n')
    return bytes(result)


def parse(raw, *, _kind=None):
    cap, node_cap = _record_profile(_kind)
    need(len(raw) <= cap, 'JSON_CAP')
    # Preserve asset parse_json's lexical/object predicates locally. Asset and
    # unrelated freezer readers retain their original profiles and budgets.
    text = raw.decode('utf-8', 'strict')
    depth, nodes, quoted, escape, scalar = 0, 0, False, False, False
    for c in text:
        if quoted:
            if escape:
                escape = False
            elif c == chr(92):
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
        need(nodes <= node_cap, 'JSON_NODE_CAP_EXCEEDED')

    def pairs(items):
        value = {}
        for key, child in items:
            need(key not in value, 'JSON_DUPLICATE_KEY')
            value[key] = child
        return value

    value = json.loads(text, object_pairs_hook=pairs,
                       parse_constant=lambda v: (_ for _ in ()).throw(FreezerError('FREEZER_JSON_NONFINITE')))
    _json_object(value, node_cap)
    return value


def name(value):
    need(type(value) is str and 0 < len(value.encode()) <= 1024 and '\\' not in value
         and not any(ord(c) < 32 or ord(c) == 127 for c in value), 'NAME_INVALID')
    parts = value.split('/')
    need(len(parts) <= 24 and all(p not in ('', '.', '..') and len(p.encode()) <= 255 for p in parts), 'NAME_INVALID')
    return value


def absolute(value):
    return ASSET['absolute'](value)


def ident(s):
    return [s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def _native_path(value: object) -> tuple[str | None, bool]:
    """Keep whole codepoints/escapes within 192 serialized ASCII characters."""
    if value is None:
        return None, False
    value = str(value)
    if len(value) <= 192 and len(json.dumps(value, ensure_ascii=True)) <= 192:
        return value, False
    prefix, size = [], 2 + len('...')
    for character in value:
        width = len(json.dumps(character, ensure_ascii=True)) - 2
        if size + width > 192:
            break
        prefix.append(character)
        size += width
    return ''.join(prefix) + '...', True


def native_tool_context(error: Exception, context: dict | None, *,
                        path: Path | None = None, info: os.stat_result | None = None) -> None:
    """Best-effort failure-only note; never a custody record or success gate."""
    try:
        if context is None:
            return
        if info is not None:
            # Only the actual pre-read refusal supplies these same-fd facts.
            context.update(terminal=path, info=info)
        prefix = 'NATIVE_TOOL_CONTEXT '
        if any(note.startswith(prefix) for note in getattr(error, '__notes__', [])):
            return
        selected, selected_truncated = _native_path(context['selected'])
        terminal, terminal_truncated = _native_path(context['terminal'])
        info = context.get('info')
        kinds = {stat.S_IFREG: 'regular', stat.S_IFDIR: 'directory', stat.S_IFLNK: 'symlink',
                 stat.S_IFCHR: 'character', stat.S_IFBLK: 'block', stat.S_IFIFO: 'fifo',
                 stat.S_IFSOCK: 'socket'}
        value = dict(role=context['role'], operation=context['operation'], selected=selected,
                     terminal=terminal, selected_truncated=selected_truncated,
                     terminal_truncated=terminal_truncated,
                     observation='opened_before_admission' if info is not None else None,
                     mode=oct(info.st_mode) if info is not None else None,
                     type_bits=oct(stat.S_IFMT(info.st_mode)) if info is not None else None,
                     type=kinds.get(stat.S_IFMT(info.st_mode), 'unknown') if info is not None else None,
                     uid=info.st_uid if info is not None else None,
                     nlink=info.st_nlink if info is not None else None)
        note = prefix + json.dumps(value, ensure_ascii=True, separators=(',', ':'))
        if len(note) <= 1024 and note.isascii() and not any(ord(c) < 32 for c in note):
            error.add_note(note)
    except Exception:
        # Formatting/add_note cannot replace the original refusal or cleanup.
        pass


class Budget:
    def __init__(self):
        self.reads = self.snapshots = self.members = self.names = self.records = 0
        self._failure = None

    def charge(self, count, key='reads'):
        limits = {'reads': 8 * 1024**3, 'snapshots': 2 * 1024**3, 'members': MAX_MEMBERS,
                  'names': 8 * MiB, 'records': JSON_IO_CAP}
        need(type(count) is int and count >= 0, 'BUDGET_INCREMENT_INVALID')
        setattr(self, key, getattr(self, key) + count)
        if self._failure is not None:
            raise self._failure
        try:
            need(getattr(self, key) <= limits[key], 'CUMULATIVE_' + key.upper() + '_CAP')
        except FreezerError as error:
            self._failure = error
            raise


@contextlib.contextmanager
def regular(path, cap, *, native_context=None):
    """Use held absolute ancestry and descriptor-relative no-follow leaf IO."""
    path = absolute(path)
    with ASSET['RootHandle'](path.parent, '/', False) as parent:
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent.fd)
        try:
            before = os.fstat(fd)
            try:
                need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1, 'REGULAR_SINGLE_LINK_REQUIRED')
                need(before.st_uid in (0, os.getuid()) and not before.st_mode & 0o022, 'SUBJECT_OWNER_MODE')
                need(before.st_size <= cap, 'SUBJECT_CAP')
            except FreezerError as error:
                native_tool_context(error, native_context, path=path, info=before)
                raise
            yield fd, before
            need(ident(os.fstat(fd)) == ident(before)
                 and ident(os.stat(path.name, dir_fd=parent.fd, follow_symlinks=False)) == ident(before), 'SUBJECT_UNSTABLE')
        finally:
            os.close(fd)


def read(path, budget, cap=RECORD_CAP, *, _json=False):
    with regular(path, cap) as (fd, info):
        budget.charge(info.st_size + 1)
        if _json:
            budget.charge(info.st_size + 1, 'records')
        data = bytearray()
        while True:
            chunk = os.read(fd, min(65536, cap - len(data) + 1, info.st_size - len(data) + 1))
            if not chunk:
                break
            data.extend(chunk)
            need(len(data) <= cap, 'SUBJECT_CAP')
            need(len(data) <= info.st_size, 'SUBJECT_SHORT_OR_GROWING')
        need(len(data) == info.st_size, 'SUBJECT_SHORT_OR_GROWING')
        return bytes(data), ident(info)


def create(path, raw, budget, *, _kind=None):
    path = absolute(path)
    cap, _ = _record_profile(_kind)
    need(len(raw) <= cap, 'WRITE_CAP')
    # Reserve the attempted exclusive write AND its mandatory guarded readback
    # before create_once. A partial IO failure cannot refund this operation.
    budget.charge(len(raw), 'records')
    budget.charge(len(raw) + 1, 'records')
    budget.charge(len(raw) + 1)
    with ASSET['root_for'](path.parent) as root:
        return ASSET['create_once'](root, path.name, raw, ASSET['ReadDomain'](len(raw)))


def observe(path, roots, budget, snapshot=None, cap=CONTAINER_CAP, *, native_context=None):
    path = absolute(path)
    matches = [absolute(p) for p in roots if path.is_relative_to(p)]
    need(matches, 'SOURCE_OUTSIDE_ROOTS')
    root = max(matches, key=lambda p: len(p.parts))
    relative = name(path.relative_to(root).as_posix())
    with ASSET['RootHandle'](root,'/',False) as domain_root:
        device = os.fstat(domain_root.fd).st_dev
    with regular(path, cap, native_context=native_context) as (fd, info):
        need(info.st_dev == device, 'CROSS_DEVICE_DESCENT')
        budget.charge(info.st_size + 1)
        h = hashlib.sha256()
        count = 0
        out = None
        with contextlib.ExitStack() as destination:
            if snapshot is not None:
                budget.charge(info.st_size, 'snapshots')
                snapshot = absolute(snapshot)
                parent = destination.enter_context(ASSET['RootHandle'](snapshot.parent, '/', True))
                out = os.open(snapshot.name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                              0o600, dir_fd=parent.fd)
            try:
                while True:
                    chunk = os.read(fd, min(65536, cap - count + 1))
                    if not chunk:
                        break
                    count += len(chunk)
                    need(count <= cap, 'SUBJECT_CAP')
                    h.update(chunk)
                    if out is not None:
                        view = memoryview(chunk)
                        while view:
                            n = os.write(out, view)
                            need(n > 0, 'SNAPSHOT_WRITE')
                            view = view[n:]
                need(count == info.st_size, 'SUBJECT_SHORT_OR_GROWING')
                if out is not None:
                    os.fsync(out)
            finally:
                if out is not None:
                    os.close(out)
        return {'root': str(root), 'relative': relative, 'identity': ident(info), 'size': count,
                'sha256': h.hexdigest(), 'snapshot': str(snapshot) if snapshot else None}


def check_subject(row, roots, budget, live=True):
    keys(row, SUBJECT_KEYS, 'SUBJECT_SCHEMA')
    need(integer(row['size']) and type(row['identity']) is list and len(row['identity']) == 8
         and all(integer(i) for i in row['identity']), 'SUBJECT_IDENTITY')
    ASSET['hex_value'](row['sha256'])
    root = absolute(row['root'])
    need(str(root) in [str(p) for p in roots], 'SUBJECT_ROOT')
    path = root / name(row['relative'])
    if live:
        actual = observe(path, roots, budget)
        need(all(actual[k] == row[k] for k in ('root', 'relative', 'identity', 'size', 'sha256')), 'LIVE_GENERATION_CHANGED')
    if row['snapshot'] is not None:
        snap = absolute(row['snapshot'])
        need(any(snap.is_relative_to(p) for p in roots), 'SNAPSHOT_OUTSIDE_ROOTS')
        actual = observe(snap, roots, budget)
        need((actual['size'], actual['sha256']) == (row['size'], row['sha256']), 'SNAPSHOT_MISMATCH')
    return path



def check_live_generation(row,session):
    path = Path(row['root'])/row['relative']
    work = session.paths.output/'scratch/freeze-work'
    if path.is_relative_to(work):
        check_subject(row,session.roots,session.budget,live=False)
        current = observe(path,session.roots,session.budget)
        need((current['size'],current['sha256']) == (row['size'],row['sha256']), 'LIVE_GENERATION_BYTES_CHANGED')
    else:
        check_subject(row,session.roots,session.budget)


def check_operation_options(option, site=None):
    required = {'site', 'declared_strip', 'declared_upx', 'availability', 'force', 'effective_strip', 'effective_upx'}
    optional = {'destination', 'target_arch', 'strict_arch_validation', 'signing_sha256', 'entitlements'}
    need(type(option) is dict and required <= set(option) <= required | optional, 'OPTION_SCHEMA')
    need(type(option['site']) is str and (site is None or option['site'] == site), 'OPTION_SITE')
    for key in ('declared_strip', 'declared_upx', 'availability', 'effective_strip', 'effective_upx'):
        need(type(option[key]) is bool, 'OPTION_TYPE:' + key)
    need(option['force'] is None or (type(option['force']) is str and len(option['force'].encode()) <= 128), 'OPTION_FORCE')
    need(option['effective_strip'] == option['declared_strip']
         and option['effective_upx'] == (option['availability'] and option['declared_upx']), 'OPTION_INCONSISTENT')
    need(not option['effective_strip'] and not option['effective_upx'], 'UNSUPPORTED_EFFECTIVE_OPTION:' + option['site'])
    if 'strict_arch_validation' in option:
        need(type(option['strict_arch_validation']) is bool, 'OPTION_TYPE:strict_arch_validation')
    if 'target_arch' in option:
        need(option['target_arch'] in (None,'arm64','x86_64','universal2'), 'OPTION_ARCH')
    if option.get('signing_sha256') is not None:
        ASSET['hex_value'](option['signing_sha256'])
    if 'destination' in option:
        name(option['destination'])
    return option


def option_identity(option):
    result = dict(option)
    if result.get('entitlements') is not None:
        result['entitlements'] = {k:v for k,v in result['entitlements'].items() if k != 'snapshot'}
    return result


def row_shape(row):
    keys(row, {'id', 'destination', 'source', 'kind', 'literal', 'subject'}, 'TOC_ROW_SCHEMA')
    need(type(row['id']) is str and row['kind'] in KINDS, 'TOC_KIND')
    if row['kind'] != 'OPTION':
        name(os.path.normpath(row['destination']))
    need(row['subject'] is None or type(row['subject']) is dict, 'TOC_SUBJECT')


def compatible(a, b):
    if a['kind'] != b['kind'] or a['literal'] != b['literal']:
        return False
    x, y = a['subject'], b['subject']
    return (x is None and y is None) or (x is not None and y is not None and (x['size'], x['sha256']) == (y['size'], y['sha256']))


def groups_for(raw, stage='COLLECT'):
    need(type(raw) is list and len(raw) <= MAX_MEMBERS, 'LEDGER_CAP')
    groups, seen = {}, set()
    for row in raw:
        row_shape(row)
        need(row['id'] not in seen, 'CONTRIBUTOR_ID_DUPLICATE')
        seen.add(row['id'])
        if row['kind'] == 'OPTION':
            continue
        key = os.path.normpath(row['destination'])
        previous = groups.setdefault(key, [])
        need(not previous or compatible(previous[0], row), stage + '_CONTRIBUTOR_CONFLICT')
        previous.append(row)
    return groups


def physical_names(rows, contents='_internal'):
    seen = {}
    for row in rows:
        dest = os.path.normpath(row['destination'])
        if row['kind'] == 'OPTION':
            continue
        physical = dest if row['kind'] in ('EXECUTABLE', 'PKG') else contents + '/' + dest
        name(physical)
        key = unicodedata.normalize('NFC', physical).casefold()
        need(key not in seen, 'PHYSICAL_ALIAS')
        need(not any(key.startswith(p + '/') or p.startswith(key + '/') for p in seen), 'PHYSICAL_PREFIX_ALIAS')
        seen[key] = physical
    return list(seen.values())


def prepare_collect_ledger(raw):
    groups = groups_for(raw)
    representatives = [v[0] for v in groups.values()]
    physical_names(representatives)
    return [{'normalized': i, 'contributors': [r['id'] for r in members]}
            for i, members in enumerate(groups.values())]


def check_collect_expansion(expected, actual):
    need(expected == actual, 'COLLECT_LEDGER_MISMATCH')


def check_normalization_edges(raw, normalized, edges, stage='COLLECT'):
    groups = groups_for(raw, stage)
    need(type(edges) is list and len(edges) == len(normalized), stage + '_NORMALIZATION_LOSS')
    options = [r for r in raw if r['kind'] == 'OPTION']
    opt = 0
    for i, (entry, edge) in enumerate(zip(normalized, edges)):
        keys(edge, {'normalized', 'contributors'}, 'NORMALIZATION_EDGE_SCHEMA')
        need(type(edge['normalized']) is int and edge['normalized'] == i, 'NORMALIZATION_ORDINAL')
        dest, src, kind = entry
        if kind == 'OPTION':
            need(opt < len(options) and (dest, src, kind) == (options[opt]['destination'], options[opt]['source'], 'OPTION'), 'OPTION_ORDER')
            members = [options[opt]]
            opt += 1
        else:
            members = groups.pop(os.path.normpath(dest), [])
            need(members and members[0]['kind'] == kind and members[0]['source'] == src, stage + '_NORMALIZATION_LOSS')
        need(edge['contributors'] == [r['id'] for r in members], stage + '_NORMALIZATION_LOSS')
    need(not groups and opt == len(options), stage + '_NORMALIZATION_LOSS')


class MarshalTOC:
    """Only the marshal forms used by PYZ's list of (name, (kind,pos,len))."""
    def __init__(self, raw, budget):
        need(len(raw) <= RECORD_CAP, 'MARSHAL_CAP')
        self.raw, self.pos, self.refs, self.budget = raw, 0, [], budget
        self.nodes = 0

    def take(self, n):
        need(integer(n) and n <= RECORD_CAP and self.pos + n <= len(self.raw), 'MARSHAL_RANGE')
        value = self.raw[self.pos:self.pos+n]
        self.pos += n
        return value

    def number(self):
        return struct.unpack('<i', self.take(4))[0]

    def item(self, depth=0):
        need(depth <= 12, 'MARSHAL_DEPTH')
        self.nodes += 1
        need(self.nodes <= 1000000, 'MARSHAL_NODE_CAP')
        tag = self.take(1)[0]
        reference, kind = bool(tag & 128), chr(tag & 127)
        slot = len(self.refs) if reference else None
        if reference:
            self.refs.append(None)
        if kind == 'i':
            value = self.number()
        elif kind in ('u', 'a', 'A', 'z', 'Z'):
            size = self.take(1)[0] if kind in ('z', 'Z') else self.number()
            need(0 <= size <= 1024, 'MARSHAL_STRING_CAP')
            value = self.take(size).decode('utf-8' if kind == 'u' else 'ascii', 'strict')
        elif kind in ('[', '(', ')'):
            size = self.take(1)[0] if kind == ')' else self.number()
            need(0 <= size <= MAX_MEMBERS, 'MARSHAL_SEQUENCE_CAP')
            value = [self.item(depth+1) for _ in range(size)]
        elif kind == 'r':
            index = self.number()
            need(not reference and 0 <= index < len(self.refs) and self.refs[index] is not None, 'MARSHAL_REFERENCE_OR_CYCLE')
            value = self.refs[index]
        else:
            raise FreezerError('FREEZER_MARSHAL_FORM:' + kind)
        if reference:
            self.refs[slot] = value
        return value

    def decode(self):
        value = self.item()
        need(self.pos == len(self.raw), 'MARSHAL_TRAILING')
        return value


def expanded(raw, expected, cap, budget):
    need(integer(expected) and expected <= cap and expected <= max(1, len(raw)) * 1000, 'EXPANSION_CAP_OR_RATIO')
    z = zlib.decompressobj()
    out = bytearray()
    for offset in range(0, len(raw), 65536):
        part = raw[offset:offset+65536]
        while part:
            got = z.decompress(part, min(65536, expected - len(out) + 1))
            budget.charge(len(got))
            out.extend(got)
            need(len(out) <= expected, 'EXPANSION_LENGTH')
            part = z.unconsumed_tail
        need(not z.unused_data, 'COMPRESSED_TRAILING')
    need(z.eof and not z.unused_data and not z.unconsumed_tail and len(out) == expected, 'COMPRESSED_INCOMPLETE')
    return bytes(out)



@contextlib.contextmanager
def container_subject(subject, session):
    path = subject['snapshot']
    need(path is not None, 'CONTAINER_SNAPSHOT_REQUIRED')
    with regular(path,CONTAINER_CAP) as (fd,info):
        need(info.st_size == subject['size'] and info.st_size > 0, 'CONTAINER_SNAPSHOT_SIZE')
        session.budget.charge(info.st_size+1)
        with mmap.mmap(fd,0,access=mmap.ACCESS_READ) as data:
            h = hashlib.sha256()
            for offset in range(0,len(data),65536):
                h.update(data[offset:offset+65536])
            need(h.hexdigest() == subject['sha256'], 'CONTAINER_SNAPSHOT_DIGEST')
            yield data


def range_digest(data,begin,end,budget):
    budget.charge(end-begin)
    h = hashlib.sha256()
    for pos in range(begin,end,65536):
        h.update(data[pos:min(end,pos+65536)])
    return h.hexdigest()


def pyz_members(raw, magic, budget):
    need(len(raw) >= 17 and raw[:4] == b'PYZ\x00' and raw[4:8] == magic, 'PYZ_MAGIC')
    need(raw[12:17] == bytes(5), 'PYZ_HEADER_PADDING')
    toc = struct.unpack('!i', raw[8:12])[0]
    need(17 <= toc <= len(raw), 'PYZ_TOC_RANGE')
    need(len(raw)-toc <= RECORD_CAP, 'MARSHAL_CAP')
    entries = MarshalTOC(raw[toc:], budget).decode()
    need(type(entries) is list and len(entries) <= MAX_MEMBERS, 'PYZ_TOC_SHAPE')
    rows, seen, ranges = [], set(), []
    expanded_total = 0
    for entry in entries:
        budget.charge(1, 'members')
        need(type(entry) is list and len(entry) == 2, 'PYZ_ENTRY')
        label, values = entry
        name(label)
        budget.charge(len(label.encode()), 'names')
        need(label not in seen and type(values) is list and len(values) == 3, 'PYZ_DUPLICATE_OR_ENTRY')
        seen.add(label)
        kind, pos, size = values
        need(all(integer(v) for v in values) and kind in (0, 1, 3), 'PYZ_TYPE')
        if kind == 3:
            need(17 <= pos <= toc and size == 0, 'PYZ_NAMESPACE')
            payload = b''
        else:
            need(17 <= pos <= pos+size <= toc and size > 0, 'PYZ_MEMBER_RANGE')
            need(not any(pos < end and begin < pos+size for begin, end in ranges), 'PYZ_OVERLAP')
            ranges.append((pos, pos+size))
            # PYZ has no advertised uncompressed size; bounded streaming with ratio.
            compressed = raw[pos:pos+size]
            z = zlib.decompressobj()
            payload = z.decompress(compressed, min(16*MiB, size*1000)+1)
            budget.charge(len(payload))
            need(len(payload) <= min(16*MiB, size*1000) and z.eof and not z.unconsumed_tail and not z.unused_data, 'PYZ_EXPANSION')
        expanded_total += len(payload)
        need(expanded_total <= CONTAINER_CAP, 'CONTAINER_EXPANSION_CAP')
        rows.append({'name': label, 'kind': kind, 'offset': pos, 'length': size,
                     'expanded_size': len(payload), 'sha256': digest(payload), 'origin': 'declared_compile_origin'})
    ordered = sorted(ranges)
    need(not ordered or (ordered[0][0] == 17 and ordered[-1][1] == toc
         and all(a[1] == b[0] for a, b in zip(ordered, ordered[1:]))), 'PYZ_UNEXPLAINED_BYTES')
    return rows


def pkg_members(raw, magic, pyversion, budget, expected_pkg=None):
    cookie = b'MEI\014\013\012\013\016'
    positions = []
    pos = raw.find(cookie)
    while pos >= 0:
        positions.append(pos)
        need(len(positions) <= MAX_MEMBERS, 'PKG_COOKIE_CAP')
        pos = raw.find(cookie, pos+1)
    valid = []
    for pos in positions:
        if pos + 88 > len(raw):
            continue
        _, length, toc, toclen, version, lib = struct.unpack('!8sIIII64s', raw[pos:pos+88])
        start, end = pos+88-length, pos+88
        if 0 <= start <= start+toc and start+toc+toclen == pos and version == pyversion and lib.split(b'\0',1)[0]:
            if expected_pkg is None or range_digest(raw,start,end,budget) == expected_pkg:
                valid.append((start, end, toc, toclen, lib))
    need(len(valid) == 1, 'PKG_COOKIE_OR_BINDING')
    start, end, toc, toclen, lib = valid[0]
    rows, seen, ranges = [], set(), []
    cursor = start+toc
    expanded_total = 0
    while cursor < start+toc+toclen:
        budget.charge(1, 'members')
        need(cursor+18 <= start+toc+toclen, 'PKG_TOC_SHORT')
        length, offset, stored, size, flag, kind = struct.unpack('!IIIIBc', raw[cursor:cursor+18])
        need(length >= 32 and length % 16 == 0 and cursor+length <= start+toc+toclen, 'PKG_TOC_LENGTH')
        field = raw[cursor+18:cursor+length]
        labelraw, zero, padding = field.partition(b'\0')
        need(zero and not padding.strip(b'\0'), 'PKG_NAME_PADDING')
        need(len(labelraw) <= 1024, 'PKG_NAME_CAP')
        budget.charge(len(labelraw), 'names')
        label = labelraw.decode('utf-8', 'strict')
        kind = kind.decode('ascii')
        need(kind in ('m','M','s','z','Z','x','b','n','o'), 'PKG_TYPE')
        if kind != 'o':
            name(label)
            need(label not in seen, 'PKG_DUPLICATE')
            seen.add(label)
        need(flag in (0, 1) and 0 <= offset <= offset+stored <= toc, 'PKG_MEMBER_RANGE')
        if stored:
            need(not any(offset < b and a < offset+stored for a,b in ranges), 'PKG_OVERLAP')
            ranges.append((offset, offset+stored))
        payloadraw = raw[start+offset:start+offset+stored]
        cap = 256*MiB if kind == 'b' else 16*MiB
        payload = expanded(payloadraw, size, cap, budget) if flag else payloadraw
        need(len(payload) == size and size <= cap, 'PKG_MEMBER_SIZE')
        expanded_total += size
        need(expanded_total <= CONTAINER_CAP, 'CONTAINER_EXPANSION_CAP')
        nested = pyz_members(payload, magic, budget) if kind == 'z' else None
        rows.append({'name': label, 'kind': kind, 'offset': offset, 'stored_size': stored, 'size': size,
                     'stored_sha256': digest(payloadraw), 'sha256': digest(payload), 'pyz': nested})
        cursor += length
    need(cursor == start+toc+toclen, 'PKG_TOC_END')
    ordered = sorted(ranges)
    need(not ordered or (ordered[0][0] == 0 and ordered[-1][1] == toc
         and all(a[1] == b[0] for a,b in zip(ordered, ordered[1:]))), 'PKG_UNEXPLAINED_BYTES')
    return {'start': start, 'end': end, 'sha256': range_digest(raw,start,end,budget),
            'python_library': lib.split(b'\0',1)[0].decode('utf-8'), 'members': rows}


def zip_metadata(raw, budget):
    """Bound metadata before ZipFile materializes its complete central directory.

    The pinned base-library writer uses small, single-disk ZIP entries without
    forced ZIP64 or data descriptors. ZIP64 and split archives are unsupported.
    Member/name charges belong here exactly once, including across containers.
    """
    size = len(raw)
    need(22 <= size <= CONTAINER_CAP and raw[size-22:size-18] == b'PK\x05\x06', 'ZIP_TRAILING_OR_EOCD')
    disk, cd_disk, disk_count, declared, cd_size, cd_start, comment = struct.unpack('<4H2IH', raw[size-18:])
    need(not comment, 'ZIP_CAP_OR_COMMENT')
    need(disk_count != 0xffff and declared != 0xffff and cd_size != 0xffffffff
         and cd_start != 0xffffffff and raw[max(0, size-42):size-38] != b'PK\x06\x07', 'ZIP64_UNSUPPORTED')
    need(disk == cd_disk == 0 and disk_count == declared, 'ZIP_DISK_OR_COUNT')
    need(cd_start + cd_size == size-22, 'ZIP_CENTRAL_RANGE')
    # At most 1KiB per name, plus one 8MiB aggregate variable-metadata fence.
    need(cd_size <= MAX_MEMBERS * (46 + 1024) + RECORD_CAP, 'ZIP_METADATA_CAP')
    cursor, count, variable = cd_start, 0, 0

    def extra(begin, length):
        end = begin + length
        while begin < end:
            need(begin+4 <= end, 'ZIP_EXTRA_RANGE')
            tag, amount = struct.unpack('<HH', raw[begin:begin+4])
            need(tag != 1, 'ZIP64_UNSUPPORTED')
            begin += 4 + amount
            need(begin <= end, 'ZIP_EXTRA_RANGE')

    while cursor < size-22:
        need(cursor+46 <= size-22 and raw[cursor:cursor+4] == b'PK\x01\x02', 'ZIP_CENTRAL_HEADER')
        header = struct.unpack('<4s6H3I5H2I', raw[cursor:cursor+46])
        flags, method = header[3:5]
        compressed, expanded_size = header[8:10]
        namesize, extrasize, commentsize, start_disk = header[10:14]
        offset = header[16]
        need(compressed != 0xffffffff and expanded_size != 0xffffffff
             and offset != 0xffffffff and start_disk != 0xffff, 'ZIP64_UNSUPPORTED')
        need(start_disk == 0, 'ZIP_DISK_OR_COUNT')
        end = cursor+46+namesize+extrasize+commentsize
        need(end <= size-22, 'ZIP_CENTRAL_RANGE')
        count += 1
        need(count <= MAX_MEMBERS, 'ZIP_CAP_OR_COMMENT')
        budget.charge(1, 'members')
        need(0 < namesize <= 1024, 'NAME_INVALID')
        label = name(raw[cursor+46:cursor+46+namesize].decode('utf-8' if flags & 0x800 else 'cp437'))
        budget.charge(len(label.encode()), 'names')
        variable += extrasize + commentsize
        need(variable <= RECORD_CAP, 'ZIP_METADATA_CAP')
        extra(cursor+46+namesize, extrasize)
        # Bound local variable metadata too; payload and name equality are
        # independently checked again by the existing reader below.
        need(0 <= offset and offset+30 <= cd_start and raw[offset:offset+4] == b'PK\x03\x04'
             and not flags & 8 and method in (0, 8), 'ZIP_LOCAL_HEADER')
        local_flags, local_method = struct.unpack('<HH', raw[offset+6:offset+10])
        local_compressed, local_expanded = struct.unpack('<II', raw[offset+18:offset+26])
        local_name, local_extra = struct.unpack('<HH', raw[offset+26:offset+30])
        need(local_compressed != 0xffffffff and local_expanded != 0xffffffff, 'ZIP64_UNSUPPORTED')
        need(local_flags == flags and local_method == method, 'ZIP_LOCAL_HEADER')
        need(0 < local_name <= 1024 and offset+30+local_name+local_extra+compressed <= cd_start, 'ZIP_LOCAL_RANGE_OR_NAME')
        variable += local_extra
        need(variable <= RECORD_CAP, 'ZIP_METADATA_CAP')
        extra(offset+30+local_name, local_extra)
        cursor = end
    need(cursor == size-22 and count == declared, 'ZIP_ENTRY_COUNT')
    return count, cd_start


class _MappedZipReader(io.IOBase):
    """Borrow the caller-held mmap without copying it or taking ownership.

    ZipFile's member reader needs seekable(), absent from CPython 3.12 mmap.
    Its cursor stays on that same mapping; seek() returns the file position
    even on Python versions where mmap.seek() returns None. Closing this
    interface leaves the mapping and stable descriptor with container_subject.
    """
    def __init__(self, mapping):
        super().__init__()
        self._mapping = mapping

    def readable(self):
        self._checkClosed()
        return not self._mapping.closed

    def seekable(self):
        self._checkClosed()
        return not self._mapping.closed

    def read(self, size=-1):
        self._checkClosed()
        return self._mapping.read(size)

    def seek(self, offset, whence=os.SEEK_SET):
        self._checkClosed()
        self._mapping.seek(offset, whence)
        return self._mapping.tell()

    def tell(self):
        self._checkClosed()
        return self._mapping.tell()


def zip_members(raw, magic, budget):
    rows, seen, total = [], set(), 0
    count, cd_start = zip_metadata(raw, budget)
    intervals = []
    with (io.BytesIO(raw) if isinstance(raw,bytes) else _MappedZipReader(raw)) as source, zipfile.ZipFile(source) as archive:
        need(len(archive.infolist()) == count and archive.start_dir == cd_start
             and not archive.comment, 'ZIP_METADATA_MISMATCH')
        for entry in archive.infolist():
            offset = entry.header_offset
            need(0 <= offset <= offset+30 <= len(raw) and raw[offset:offset+4] == b'PK\x03\x04'
                 and not entry.flag_bits & 8 and entry.compress_type in (0,8), 'ZIP_LOCAL_HEADER')
            namesize,extrasize = struct.unpack('<HH',raw[offset+26:offset+30])
            data_start = offset+30+namesize+extrasize
            end = data_start+entry.compress_size
            need(end <= archive.start_dir and raw[offset+30:offset+30+namesize].decode('utf-8' if entry.flag_bits & 0x800 else 'cp437') == entry.filename, 'ZIP_LOCAL_RANGE_OR_NAME')
            intervals.append((offset,end))
            label = name(entry.filename)
            need(label not in seen and not entry.flag_bits & 1 and not entry.is_dir(), 'ZIP_DUPLICATE_OR_KIND')
            seen.add(label)
            need(entry.file_size <= 16*MiB and entry.file_size <= max(1, entry.compress_size)*1000, 'ZIP_EXPANSION_CAP')
            total += entry.file_size
            need(total <= CONTAINER_CAP, 'CONTAINER_EXPANSION_CAP')
            h, count, prefix = hashlib.sha256(), 0, b''
            with archive.open(entry) as stream:
                while True:
                    chunk = stream.read(min(65536, entry.file_size-count+1))
                    if not chunk:
                        break
                    count += len(chunk)
                    budget.charge(len(chunk))
                    need(count <= entry.file_size, 'ZIP_MEMBER_LENGTH')
                    if not prefix:
                        prefix = chunk[:16]
                    h.update(chunk)
            need(count == entry.file_size, 'ZIP_MEMBER_LENGTH')
            if label.endswith('.pyc'):
                need(len(prefix) >= 16 and prefix[:4] == magic, 'ZIP_PYC_MAGIC')
            rows.append({'name': label, 'size': count, 'sha256': h.hexdigest(), 'crc': entry.CRC,
                         'origin': 'declared_compile_origin'})
    ordered = sorted(intervals)
    need(not ordered or (ordered[0][0] == 0 and ordered[-1][1] == archive.start_dir
         and all(a[1] == b[0] for a,b in zip(ordered,ordered[1:]))), 'ZIP_OVERLAP_OR_UNEXPLAINED_BYTES')
    return rows


def resolve_link(root, relative, budget):
    """Resolve recorded relative aliases one component at a time, within root."""
    root = absolute(root)
    pending, done, aliases, visited = relative.split('/'), [], [], set()
    while pending:
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            need(done, 'LINK_ESCAPE')
            done.pop()
            continue
        current = root.joinpath(*done, part)
        with ASSET['RootHandle'](current.parent, '/', False) as parent:
            first = os.stat(part, dir_fd=parent.fd, follow_symlinks=False)
            if stat.S_ISLNK(first.st_mode):
                target = os.readlink(part, dir_fd=parent.fd)
                need(ident(first) == ident(os.stat(part, dir_fd=parent.fd, follow_symlinks=False)), 'LINK_UNSTABLE')
                need(not os.path.isabs(target) and 0 < len(target.encode()) <= 1024, 'LINK_TARGET_INVALID')
                need(str(current) not in visited and len(aliases) < 16, 'LINK_CYCLE_OR_HOPS')
                visited.add(str(current))
                aliases.append({'relative': current.relative_to(root).as_posix(), 'target': target, 'identity': ident(first)})
                pending = target.split('/') + pending
            else:
                need(first.st_uid in (0, os.getuid()) and not first.st_mode & 0o022, 'LINK_TERMINAL_OWNER_MODE')
                need(stat.S_ISDIR(first.st_mode) if pending else (stat.S_ISREG(first.st_mode) or stat.S_ISDIR(first.st_mode)), 'LINK_TERMINAL_KIND')
                done.append(part)
    return root.joinpath(*done), aliases


def tree(root, budget):
    root = absolute(root)
    rows, folded, observed = [], set(), []
    with ASSET['RootHandle'](root,'/',True) as pinned:
        device = os.fstat(pinned.fd).st_dev
    def walk(path, depth):
        need(depth <= 24, 'TREE_DEPTH')
        with ASSET['RootHandle'](path, '/', True) as handle:
            root_before = ident(os.fstat(handle.fd))
            before = {leaf:ident(os.stat(leaf,dir_fd=handle.fd,follow_symlinks=False)) for leaf in sorted(os.listdir(handle.fd))}
            for leaf in before:
                relative = name((path / leaf).relative_to(root).as_posix())
                budget.charge(1, 'members')
                budget.charge(len(relative.encode()), 'names')
                need(len(rows) < 8192, 'TREE_ENTRY_CAP')
                canonical = unicodedata.normalize('NFC', relative).casefold()
                need(canonical not in folded, 'TREE_ALIAS')
                folded.add(canonical)
                s = os.stat(leaf, dir_fd=handle.fd, follow_symlinks=False)
                need(ident(s) == before[leaf], 'TREE_MEMBER_CHANGED')
                observed.append((path/leaf,ident(s)))
                need(s.st_dev == device, 'CROSS_DEVICE_DESCENT')
                need(s.st_uid == os.getuid() and (stat.S_ISLNK(s.st_mode) or not s.st_mode & 0o022), 'TREE_OWNER_MODE')
                row = {'relative': relative, 'mode': stat.S_IMODE(s.st_mode), 'kind': None,
                       'sha256': None, 'size': None, 'target': None}
                if stat.S_ISDIR(s.st_mode):
                    row['kind'] = 'directory'
                    rows.append(row)
                    walk(path / leaf, depth+1)
                elif stat.S_ISREG(s.st_mode):
                    row['kind'] = 'file'
                    subject = observe(path / leaf, [root], budget)
                    row.update(sha256=subject['sha256'], size=subject['size'])
                    rows.append(row)
                elif stat.S_ISLNK(s.st_mode):
                    target = os.readlink(leaf, dir_fd=handle.fd)
                    need(ident(s) == ident(os.stat(leaf, dir_fd=handle.fd, follow_symlinks=False)), 'LINK_UNSTABLE')
                    terminal, aliases = resolve_link(root, relative, budget)
                    if terminal.is_dir():
                        with ASSET['RootHandle'](terminal,'/',True):
                            pass
                    else:
                        observe(terminal, [root], budget)
                    row.update(kind='link', target=target)
                    rows.append(row)
                else:
                    raise FreezerError('FREEZER_TREE_SPECIAL_MEMBER')
            after = {leaf:ident(os.stat(leaf,dir_fd=handle.fd,follow_symlinks=False)) for leaf in sorted(os.listdir(handle.fd))}
            need(after == before and ident(os.fstat(handle.fd)) == root_before, 'TREE_MEMBERSHIP_CHANGED')
    walk(root, 0)
    # Recheck every measured edge after the complete walk, including hidden and
    # nested subjects. The serial producer owns writes outside this read interval.
    for path,old in observed:
        with ASSET['RootHandle'](path.parent,'/',True) as parent:
            need(ident(os.stat(path.name,dir_fd=parent.fd,follow_symlinks=False)) == old, 'TREE_MEMBER_CHANGED')
    return sorted(rows, key=lambda r: r['relative'])


def compare_copy(before, after, mode_changes=None):
    need(len(before) == len(after), 'COPY_MEMBERSHIP')
    mode_changes = mode_changes or {}
    changes = []
    for a, b in zip(before, after):
        keys(a, {'relative','mode','kind','sha256','size','target'}, 'CENSUS_SCHEMA')
        keys(b, a, 'CENSUS_SCHEMA')
        need(all(a[k] == b[k] for k in a if k != 'mode'), 'COPY_BYTES_TYPE_OR_TARGET')
        if a['mode'] != b['mode']:
            need(a['kind'] == 'file' and mode_changes.get(a['relative']) == b['mode'], 'COPY_MODE')
            changes.append({'relative': a['relative'], 'before': a['mode'], 'after': b['mode']})
    return changes


class Session:
    def __init__(self, candidate, paths, run_id, context_sha256=None, *, _budget=None):
        self.candidate, self.paths, self.run = candidate, paths, run_id
        self.context_sha256 = context_sha256
        self.budget = Budget() if _budget is None else _budget
        self.roots = [str(paths.exported_source), str(paths.output)]
        self.start = time.monotonic_ns()
        self.stage = paths.output / 'scratch/freezer-subjects'
        self.ordinal = 0
        self.aliases = []

    def check_live(self):
        ASSET['bindings'](self.candidate, self.paths, self.run)
        with ASSET['root_for'](self.paths.evidence_dir) as root:
            for leaf in ('freezer-failure.json',):
                try:
                    os.stat(leaf, dir_fd=root.fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FreezerError('FREEZER_REFUSED_ATTEMPT')

    def write(self, kind, payload, upstream):
        self.check_live()
        need(kind in FILES, 'RECORD_KIND')
        keys(payload,PAYLOAD_FIELDS[kind],'PAYLOAD_SCHEMA:'+kind)
        value = {'encoding': ENCODING, 'kind': kind, 'candidate': asdict(self.candidate), 'run_id': self.run,
                 'observed_start_ns': self.start, 'observed_end_ns': time.monotonic_ns(), 'payload': payload}
        if kind != 'context':
            value.update(context_sha256=self.context_sha256, upstream=upstream)
        raw = encoded(value, _kind=kind)
        parse(raw, _kind=kind)
        create(self.paths.evidence_dir / FILES[kind], raw, self.budget, _kind=kind)
        return {'path': str(self.paths.evidence_dir / FILES[kind]), 'size': len(raw), 'sha256': digest(raw)}

    def load(self, kind, expected):
        self.check_live()
        ASSET['hex_value'](expected)
        cap, _ = _record_profile(kind)
        raw, _ = read(self.paths.evidence_dir / FILES[kind], self.budget, cap, _json=True)
        need(digest(raw) == expected, 'EXPECTED_DIGEST:' + kind)
        row = parse(raw, _kind=kind)
        fields = {'encoding','kind','candidate','run_id','observed_start_ns','observed_end_ns','payload'}
        keys(row, fields if kind == 'context' else fields | {'context_sha256','upstream'}, 'RECORD_SCHEMA')
        need(row['encoding'] == ENCODING and row['kind'] == kind and row['candidate'] == asdict(self.candidate)
             and row['run_id'] == self.run, 'RECORD_BINDING:' + kind)
        need(integer(row['observed_start_ns']) and integer(row['observed_end_ns'])
             and row['observed_start_ns'] <= row['observed_end_ns'], 'RECORD_TIME')
        if kind != 'context':
            need(row['context_sha256'] == self.context_sha256, 'RECORD_CONTEXT')
        keys(row['payload'], PAYLOAD_FIELDS[kind], 'PAYLOAD_SCHEMA:' + kind)
        return row

    def reference(self, kind, expected):
        cap, _ = _record_profile(kind)
        raw,_ = read(self.paths.evidence_dir/FILES[kind],self.budget,cap,_json=True)
        need(digest(raw) == expected, 'REFERENCE_DIGEST')
        return {'path':str(self.paths.evidence_dir/FILES[kind]), 'size':len(raw), 'sha256':expected}

    def context(self):
        row = self.load('context', self.context_sha256)['payload']
        dirs, owner, preflight = ASSET['bindings'](self.candidate, self.paths, self.run)
        need(row['paths'] == {k:str(v) for k,v in asdict(self.paths).items()} and row['directories'] == dirs
             and row['owner'] == owner and row['preflight'] == preflight, 'CONTEXT_LIVE_BINDING')
        ASSET['check_context'](self.candidate, self.paths, self.run, row['asset_context'])
        raw, _ = read(self.paths.evidence_dir / 'toolchains.txt', self.budget, MiB)
        need(digest(raw) == row['toolchains'], 'TOOLCHAINS_BINDING')
        for subject in row['sources']:
            check_subject(subject, self.roots, self.budget)
        for key in ('wheel', 'provenance'):
            check_subject(row[key], self.roots, self.budget)
        need(row['contract'] == 'pyinstaller-6.21.0-1happyranchcustody5', 'CONTRACT')
        return row

    def snapshot(self, path, lane, stage, cap=CONTAINER_CAP, *, native_context=None):
        original = absolute(path)
        choices = [absolute(p) for p in self.roots if original.is_relative_to(p)]
        need(choices, 'SOURCE_OUTSIDE_ROOTS')
        root = max(choices,key=lambda p:len(p.parts))
        path, aliases = resolve_link(root,original.relative_to(root).as_posix(),self.budget)
        if native_context is not None:
            native_context['terminal'] = path
        if aliases:
            self.aliases.append({'root':str(root),'original':original.relative_to(root).as_posix(),
                                 'terminal':path.relative_to(root).as_posix(),'aliases':aliases})
        directory = self.stage / stage / lane
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.ordinal += 1
        return observe(path, self.roots, self.budget, directory / f'{self.ordinal}.bin', cap,
                       native_context=native_context)

    def rows(self, toc, lane, stage):
        result = []
        for ordinal, (dest, src, kind) in enumerate(toc):
            self.budget.charge(1, 'members')
            need(kind in KINDS, 'TOC_KIND')
            structural = kind.startswith('PYMODULE') and src in (None, '-')
            literal = src if kind == 'SYMLINK' else dest if kind == 'OPTION' else None
            subject = None if structural or literal is not None else self.snapshot(src, lane, stage, (CONTAINER_CAP if kind in ('PYZ','EXECUTABLE') or dest == 'base_library.zip' else 256*MiB if kind in ('BINARY','EXTENSION') else 16*MiB))
            row = {'id': f'{lane}:{stage}:{ordinal}', 'destination': dest, 'source': src, 'kind': kind,
                   'literal': literal, 'subject': subject}
            row_shape(row)
            result.append(row)
        return result

    def custody(self, lane, target, ledger=None):
        directory = self.stage / target / lane
        directory.mkdir(parents=True, mode=0o700)
        return {'run_id': self.run, 'stage_dir': str(directory), 'source_roots': list(self.roots),
                'lane': lane, 'target': target, 'ordinal': 0, 'ledger_ref': ledger}

    def fail(self, error, original_exit=None):
        try:
            with ASSET['SUPERVISOR'].failure_scope(original_exit or 1):
                create(self.paths.evidence_dir / 'freezer-failure.json', encoded({
                    'encoding': ENCODING, 'state': 'INCOMPLETE', 'run_id': self.run,
                    'error': str(error)[:4096], 'original_exit': original_exit, 'refused_owners': ['freezer']}), self.budget)
        except (OSError, ValueError) as persistence:
            print('FREEZER_FAILURE_PERSISTENCE:' + type(persistence).__name__, file=sys.stderr)


def begin(candidate, paths, run, asset_context, toolchains, *, _budget=None):
    session = Session(candidate, paths, run, _budget=_budget)
    try:
        session.check_live()
        create(paths.evidence_dir / 'freezer-attempt.json', encoded({'encoding':ENCODING, 'run_id':run, 'state':'INCOMPLETE'}), session.budget)
        ASSET['check_context'](candidate, paths, run, asset_context)
        dirs, owner, preflight = ASSET['bindings'](candidate, paths, run)
        tool_raw, _ = read(paths.evidence_dir / 'toolchains.txt', session.budget, MiB)
        need(digest(tool_raw) == toolchains, 'TOOLCHAINS_BINDING')
        sources = [observe(paths.exported_source / p, session.roots, session.budget, cap=16*MiB) for p in
                   ('packaging/daemon.spec', 'app/mac/scripts/freezer_evidence.py', 'app/mac/scripts/asset_evidence.py',
                    'pyproject.toml','uv.lock', VENDOR+'/prepare.py', VENDOR+'/custody.patch')]
        vendor = paths.exported_source / VENDOR
        provenance_raw, _ = read(vendor / 'provenance.json', session.budget, 256*1024,_json=True)
        provenance = parse(provenance_raw)
        wheel = observe(vendor / 'dist' / WHEEL, session.roots, session.budget)
        need((wheel['size'],wheel['sha256']) == (provenance['replacement']['size'],provenance['replacement']['sha256']), 'SOURCE_WHEEL_DIGEST')
        session.stage.mkdir(mode=0o700)
        (session.stage / 'return').mkdir(mode=0o700)
        reference = session.write('context', {'paths': {k:str(v) for k,v in asdict(paths).items()},
            'directories': dirs, 'owner': owner, 'preflight':preflight, 'asset_context':asset_context,
            'toolchains':toolchains, 'sources':sources, 'wheel':wheel,
            'provenance':observe(vendor / 'provenance.json', session.roots, session.budget, cap=256*1024),
            'contract':'pyinstaller-6.21.0-1happyranchcustody5'}, None)
        return {'context':reference['sha256'], 'caller':{'candidate':asdict(candidate), 'paths':{k:str(v) for k,v in asdict(paths).items()},
                                                       'run_id':run,'context':reference['sha256'],
                                                       'helper_sha256':sources[1]['sha256']}}
    except BaseException as error:
        session.fail(error)
        raise


def installed_pyinstaller(session):
    vendor = session.paths.exported_source / VENDOR
    provenance = parse(read(vendor/'provenance.json', session.budget, 256*1024,_json=True)[0])
    distribution = importlib.metadata.distribution('pyinstaller')
    need(distribution.version == '6.21.0', 'INSTALLED_VERSION')
    records = []
    for member in ('PyInstaller/building/api.py','PyInstaller/building/utils.py',
                   'pyinstaller-6.21.0.dist-info/METADATA','pyinstaller-6.21.0.dist-info/WHEEL',
                   'pyinstaller-6.21.0.dist-info/RECORD','pyinstaller-6.21.0.dist-info/licenses/COPYING.txt'):
        subject = session.snapshot(distribution.locate_file(member), 'shared', 'installed')
        expected = provenance['members'][member]
        if not member.endswith('.dist-info/RECORD'):
            need((subject['size'], subject['sha256']) == (expected['size'],expected['sha256']), 'INSTALLED_MEMBER_DIGEST')
        records.append({'member':member,'subject':subject})
    installed_record = next(r['subject'] for r in records if r['member'].endswith('.dist-info/RECORD'))
    raw = read(installed_record['snapshot'],session.budget,256*1024)[0]
    claims = {}
    for row in csv.reader(io.StringIO(raw.decode('utf-8'))):
        need(len(row) == 3 and row[0] not in claims, 'INSTALLED_RECORD_DUPLICATE')
        claims[row[0]] = row[1:]
    for row in records:
        if row['member'].endswith('.dist-info/RECORD'):
            need(claims.get(row['member']) == ['', ''], 'INSTALLED_RECORD_SELF')
            continue
        subject = row['subject']
        h = 'sha256='+base64.urlsafe_b64encode(bytes.fromhex(subject['sha256'])).rstrip(b'=').decode()
        need(claims.get(row['member']) == [h,str(subject['size'])], 'INSTALLED_RECORD_MEMBER_DIGEST')
    for member in ('api','utils'):
        loaded = sys.modules.get('PyInstaller.building.'+member)
        need(loaded is not None and Path(loaded.__file__) == Path(distribution.locate_file('PyInstaller/building/'+member+'.py')), 'LOADED_SOURCE_PATH')
    return records



def executable_alias(value,session):
    current, aliases, seen = absolute(value), [], set()
    for _ in range(17):
        need(any(current.is_relative_to(root) for root in map(Path,session.roots)), 'EXECUTABLE_ROOT')
        with ASSET['RootHandle'](current.parent,'/',False) as parent:
            first = os.stat(current.name,dir_fd=parent.fd,follow_symlinks=False)
            if not stat.S_ISLNK(first.st_mode):
                need(stat.S_ISREG(first.st_mode), 'EXECUTABLE_KIND')
                return current,aliases
            target = os.readlink(current.name,dir_fd=parent.fd)
            need(ident(first) == ident(os.stat(current.name,dir_fd=parent.fd,follow_symlinks=False)), 'EXECUTABLE_ALIAS_UNSTABLE')
            need(str(current) not in seen and len(aliases) < 16 and first.st_uid in (0,os.getuid()), 'EXECUTABLE_ALIAS_CYCLE')
            seen.add(str(current))
            aliases.append({'path':str(current),'target':target,'identity':ident(first)})
            current = absolute(os.path.normpath(target if os.path.isabs(target) else str(current.parent/target)))
    raise FreezerError('FREEZER_EXECUTABLE_ALIAS_HOPS')


def check_toolchain(session,tool):
    check_subject(tool['receipt'],session.roots,session.budget)
    receipt = parse(read(tool['receipt']['snapshot'],session.budget,MiB,_json=True)[0])
    need(receipt == {'executable':tool['executable'],'version':tool['version'],'platform':'darwin','machine':tool['machine']}, 'EFFECTIVE_PYTHON_RECEIPT')
    executable, aliases = executable_alias(tool['executable'],session)
    need(aliases == tool['python_alias'] and str(executable) == str(Path(tool['python_executable']['root'])/tool['python_executable']['relative']), 'PYTHON_ALIAS_BINDING')
    check_subject(tool['python_executable'],session.roots,session.budget)
    need([t['tool'] for t in tool['native_tools']] == ['codesign','lipo','install_name_tool'], 'NATIVE_TOOL_SET')
    for observed in tool['native_tools']:
        keys(observed, {'tool','selected','aliases','subject','relation'}, 'NATIVE_TOOL_SCHEMA')
        need(observed['relation'] == 'selection_observation', 'NATIVE_TOOL_RELATION')
        executable, aliases = executable_alias(observed['selected'],session)
        need(aliases == observed['aliases'] and str(executable) == str(Path(observed['subject']['root'])/observed['subject']['relative']), 'NATIVE_TOOL_ALIAS')
        check_subject(observed['subject'],session.roots,session.budget)


def _admit_process_roots(roles: tuple[tuple[str, Path], ...], roots: list[str]) -> None:
    """Validate every occurrence before incremental, admission-only deduplication."""
    for role, root in roles:
        try:
            with ASSET['RootHandle'](root, '/', False):
                pass
        except ASSET['AssetError'] as error:
            ASSET['directory_context'](error, role=role, root=root)
            raise
        if str(root) not in roots:
            roots.append(str(root))


def native_selection_path(value):
    """Validate raw resolver spelling without resolving away unsafe ancestry."""
    need(type(value) is str and 0 < len(value) <= 4096 and ':' not in value
         and not any(unicodedata.category(c).startswith('C') for c in value), 'NATIVE_SELECTION_PATH')
    try:
        return absolute(value)
    except (ASSET['AssetError'], UnicodeError):
        raise FreezerError('FREEZER_NATIVE_SELECTION_PATH') from None


def native_selection_paths(value):
    """Transient spec-only selection, never a persisted root or trust grant."""
    keys(value, {'developer','lipo','install_name_tool'}, 'NATIVE_SELECTION_SCHEMA')
    developer = native_selection_path(value['developer'])
    need(value['developer'] == '/Library/Developer/CommandLineTools', 'NATIVE_DEVELOPER_SELECTION')
    result = {'developer': developer}
    for role in ('lipo','install_name_tool'):
        try:
            path = native_selection_path(value[role])
            need(value[role] == '/Library/Developer/CommandLineTools/usr/bin/' + role,
                 'NATIVE_REAL_TOOL_SELECTION')
            result[role] = path
        except FreezerError as error:
            native_tool_context(error, dict(role=role, selected=value[role], terminal=None,
                                            operation='selection'))
            raise
    return result


def spec_begin(project, *, native_selection=None):
    raw = os.environ.get('HAPPYRANCH_FREEZER_CALLER')
    if raw is None:
        return None
    budget = Budget()
    budget.charge(len(raw.encode()), 'records')
    caller = parse(raw.encode())
    keys(caller, {'candidate','paths','run_id','context','helper_sha256'}, 'CALLER_SCHEMA')
    candidate = ASSET['Candidate'](**caller['candidate'])
    paths = ASSET['Paths'](**{k:Path(v) for k,v in caller['paths'].items()})
    need(paths.exported_source == project, 'SPEC_CALLER_SOURCE')
    session = Session(candidate, paths, caller['run_id'], caller['context'], _budget=budget)
    session.context()
    need(sys.platform == 'darwin' and platform.machine() == 'arm64' and (3,12) <= sys.version_info[:2] <= (3,14), 'NATIVE_PLATFORM')
    # These explicit observed process roots are not installer or image attestation.
    _admit_process_roots((('prefix', Path(sys.prefix)), ('base_prefix', Path(sys.base_prefix)),
                          ('stdlib', Path(sysconfig.get_path('stdlib'))), ('usr-bin', Path('/usr/bin'))),
                         session.roots)
    need(len(session.roots) <= 16, 'ROOT_CAP')
    session.analysis, session.lanes = {}, {}
    session.installed = installed_pyinstaller(session)
    session.toolchain = {'executable':sys.executable,'version':sys.version,'implementation':sys.implementation.name,
                         'machine':platform.machine(),'base_prefix':sys.base_prefix,'stdlib':sysconfig.get_path('stdlib'),
                         'bytecode_magic':importlib.util.MAGIC_NUMBER.hex(), 'python_version':sys.version_info[0]*100+sys.version_info[1],
                         'origin':'origin_unobserved', 'receipt':session.snapshot(paths.output/'scratch/freeze-work/python-toolchain.json','shared','toolchain',MiB),
                         'python_executable':None,'python_alias':[], 'native_tools':[]}
    receipt = parse(read(session.toolchain['receipt']['snapshot'],session.budget,MiB,_json=True)[0])
    need(receipt == {'executable':sys.executable,'version':sys.version,'platform':sys.platform,'machine':platform.machine()}, 'EFFECTIVE_PYTHON_RECEIPT')
    executable, aliases = executable_alias(sys.executable,session)
    session.toolchain['python_alias'] = aliases
    session.toolchain['python_executable'] = session.snapshot(executable,'shared','toolchain',256*MiB)
    # Revalidate the internal selection only at the native stage, after the
    # original caller/context/process-root/wheel/receipt/Python checks.
    native = native_selection_paths(native_selection)
    for tool in ('lipo','install_name_tool'):
        try:
            _admit_process_roots(((tool, native[tool].parent),), session.roots)
        except (FreezerError, ASSET['AssetError'], OSError) as error:
            native_tool_context(error, dict(role=tool, selected=str(native[tool]), terminal=None,
                                            operation='selection'))
            raise
    need(len(session.roots) <= 16, 'ROOT_CAP')
    for tool, selected in (('codesign','/usr/bin/codesign'),('lipo',str(native['lipo'])),
                           ('install_name_tool',str(native['install_name_tool']))):
        native_context = dict(role=tool, selected=selected, terminal=None, operation='selection')
        try:
            need(selected is not None, 'NATIVE_TOOL_UNAVAILABLE')
            native_context['operation'] = 'executable_alias'
            tool_path, aliases = executable_alias(selected,session)
            if tool != 'codesign':
                need(not aliases and str(tool_path) == selected, 'NATIVE_PHYSICAL_TOOL_REQUIRED')
                need(os.access(selected, os.X_OK), 'NATIVE_TOOL_NOT_EXECUTABLE')
            native_context['operation'] = 'native_snapshot'
            session.toolchain['native_tools'].append({'tool':tool,'selected':selected,'aliases':aliases,
                        'subject':session.snapshot(tool_path,'shared','toolchain',256*MiB,
                                                   native_context=native_context), 'relation':'selection_observation'})
        except (FreezerError, ASSET['AssetError'], OSError) as error:
            native_tool_context(error, native_context)
            raise
    original_path = os.environ.get('PATH')
    parents = list(dict.fromkeys(str(native[tool].parent) for tool in ('lipo','install_name_tool')))
    proposed_path = os.pathsep.join(parents) + (os.pathsep + original_path if original_path is not None else '')
    try:
        for tool in ('lipo','install_name_tool'):
            need(shutil.which(tool, path=proposed_path) == str(native[tool]), 'NATIVE_PATH_SELECTION_CHANGED')
        # Vendor PATH argv inherits this for both lanes and PYZ/EXE/COLLECT.
        # Fresh external readers reopen recorded paths; the wrapper is unchanged.
        os.environ['PATH'] = proposed_path
    except BaseException as error:
        if original_path is None:
            os.environ.pop('PATH', None)
        else:
            os.environ['PATH'] = original_path
        native_tool_context(error, dict(role=tool, selected=str(native[tool]), terminal=None,
                                        operation='selection'))
        raise
    return session



def attribute_sources(session):
    """RECORD is an owner candidate until the exact source size/hash is checked."""
    sources = {}
    for lane in ('daemon','cli'):
        for rows in session.analysis[lane].values():
            for row in rows:
                if row['subject']:
                    sources.setdefault(str(Path(row['subject']['root'])/row['subject']['relative']),row['subject'])
    result, owners = [], {}
    for distribution in importlib.metadata.distributions():
        files = list(distribution.files or [])
        selected = [(f, sources.get(str(distribution.locate_file(f)))) for f in files]
        selected = [(f,subject) for f,subject in selected if subject is not None]
        if not selected:
            continue
        owner = distribution.metadata.get('Name')
        need(type(owner) is str and owner and len(owner.encode()) <= 128, 'DISTRIBUTION_NAME')
        candidates = [f for f in files if str(f).endswith('.dist-info/RECORD')]
        notices = [f for f in files if any(word in f.name.lower() for word in ('license','copying','notice','copyright'))]
        retained, members, refused = [], [], []
        record = None
        try:
            need(len(candidates) == 1, 'DISTRIBUTION_RECORD')
            record = session.snapshot(distribution.locate_file(candidates[0]),'shared','original_record',256*1024)
            raw = read(record['snapshot'],session.budget,256*1024)[0]
            rows = list(csv.reader(io.StringIO(raw.decode('utf-8'))))
            need(len(rows) <= MAX_MEMBERS, 'DISTRIBUTION_RECORD_CAP')
            mapping = {}
            for row in rows:
                need(len(row) == 3 and row[0] not in mapping, 'DISTRIBUTION_RECORD_DUPLICATE')
                mapping[row[0]] = row[1:]
            for member, subject in selected:
                name_in_record = str(member)
                need(name_in_record in mapping, 'DISTRIBUTION_RECORD_MEMBER')
                h,n = mapping[name_in_record]
                expected = 'sha256='+base64.urlsafe_b64encode(bytes.fromhex(subject['sha256'])).rstrip(b'=').decode()
                need(h == expected and n == str(subject['size']), 'DISTRIBUTION_RECORD_DIGEST')
                physical = str(Path(subject['root'])/subject['relative'])
                need(physical not in owners or owners[physical] == owner, 'AMBIGUOUS_OWNER')
                owners[physical] = owner
                check_subject(subject,session.roots,session.budget)
                members.append({'member':name_in_record,'record_digest':h,'record_size':n,'subject':subject})
            for notice in notices:
                subject = session.snapshot(distribution.locate_file(notice),'shared','original_notice',256*1024)
                need(0 < subject['size'] <= 256*1024, 'NOTICE_CAP_OR_EMPTY')
                # No rights/applicability assertion follows from retaining original bytes.
                retained.append({'member':str(notice),'subject':subject})
            need(retained, 'NOTICE_MISSING')
        except (OSError,ValueError) as error:
            refused.append(str(error)[:1024])
        result.append({'owner':owner,'version':distribution.version,'record':record,'members':members,
                       'notices':retained,'refused':refused,'applicability':'unresolved'})
    unmapped = sorted(set(sources)-set(owners))
    result.append({'owner':None,'version':None,'record':None,'members':[], 'notices':[],
                   'refused':['origin_unobserved:'+p for p in unmapped], 'applicability':'unresolved'})
    return result


def validate_attribution(session, records):
    need(type(records) is list and len(records) <= MAX_MEMBERS, 'ATTRIBUTION_CAP')
    owner_seen = set()
    for owner in records:
        keys(owner, {'owner','version','record','members','notices','refused','applicability'}, 'ATTRIBUTION_SCHEMA')
        need(owner['applicability'] == 'unresolved' and type(owner['refused']) is list, 'APPLICABILITY_BOUNDARY')
        if owner['owner'] is not None:
            need(owner['owner'] not in owner_seen, 'DUPLICATE_OWNER')
            owner_seen.add(owner['owner'])
        need(owner['record'] is not None or not owner['members'], 'ATTRIBUTION_RECORD_REQUIRED')
        if owner['record']:
            check_subject(owner['record'],session.roots,session.budget)
            raw = read(owner['record']['snapshot'],session.budget,256*1024)[0]
            mapping = {}
            for row in csv.reader(io.StringIO(raw.decode('utf-8'))):
                need(len(row) == 3 and row[0] not in mapping, 'DISTRIBUTION_RECORD_DUPLICATE')
                mapping[row[0]] = row[1:]
            member_names = set()
            for member in owner['members']:
                keys(member, {'member','record_digest','record_size','subject'}, 'ATTRIBUTION_MEMBER_SCHEMA')
                check_subject(member['subject'],session.roots,session.budget)
                need(member['member'] not in member_names, 'ATTRIBUTION_DUPLICATE_MEMBER')
                member_names.add(member['member'])
                subject = member['subject']
                record_path = Path(owner['record']['root'])/owner['record']['relative']
                declared = absolute(os.path.normpath(str(record_path.parent.parent/member['member'])))
                roots = [Path(p) for p in session.roots if declared.is_relative_to(p)]
                need(roots, 'ATTRIBUTION_MEMBER_ROOT')
                root = max(roots,key=lambda p:len(p.parts))
                resolved,_ = resolve_link(root,declared.relative_to(root).as_posix(),session.budget)
                need(resolved == Path(subject['root'])/subject['relative'], 'ATTRIBUTION_MEMBER_PATH')
                h = 'sha256='+base64.urlsafe_b64encode(bytes.fromhex(subject['sha256'])).rstrip(b'=').decode()
                need(mapping.get(member['member']) == [h,str(subject['size'])] == [member['record_digest'],member['record_size']], 'REOPEN_RECORD_DIGEST')
        for notice in owner['notices']:
            keys(notice, {'member','subject'}, 'NOTICE_SCHEMA')
            check_subject(notice['subject'],session.roots,session.budget)


def snapshot_analysis(session, lane, analysis):
    if session:
        session.analysis[lane] = {kind:session.rows(getattr(analysis,kind),lane,'analysis_'+kind)
                                  for kind in ('scripts','pure','binaries','datas')}



def match_prior_rows(expected, actual, stage):
    remaining = list(actual)
    for row in expected:
        candidates = [(i,r) for i,r in enumerate(remaining) if (r['destination'],r['source'],r['kind'])
                      == (row['destination'],row['source'],row['kind'])]
        need(candidates, stage+'_MISSING_PRIOR_OCCURRENCE')
        i, current = candidates[0]
        need(compatible(row,current), stage+'_PRIOR_GENERATION_MISMATCH')
        remaining.pop(i)
    return remaining



def prepare_exe_arguments(session,lane,pyz,scripts,extras):
    arguments = (pyz,scripts,extras)
    if session:
        expected = []
        for ordinal,arg in enumerate(arguments):
            toc = [(Path(arg.name).name,arg.name,'PYZ'), *arg.dependencies] if ordinal == 0 else arg
            expected.append({'ordinal':ordinal,'kind':'PYZ' if ordinal == 0 else 'TOC',
                             'rows':session.rows(toc,lane,'EXE_argument_'+str(ordinal))})
        session.lanes[lane]['EXE_arguments'] = expected
    return arguments


def check_exe_arguments(expected,actual):
    need(len(expected) == len(actual), 'EXE_ARGUMENT_COUNT')
    for ordinal,(a,b) in enumerate(zip(expected,actual)):
        keys(a, {'ordinal','kind','rows'}, 'EXE_ARGUMENT_SCHEMA')
        keys(b, {'ordinal','kind','rows'}, 'EXE_ARGUMENT_SCHEMA')
        need(type(a['ordinal']) is int and type(b['ordinal']) is int and a['ordinal'] == b['ordinal'] == ordinal
             and a['kind'] == b['kind'], 'EXE_ARGUMENT_IDENTITY')
        need(not match_prior_rows(a['rows'],b['rows'],'EXE_ARGUMENT'), 'EXE_ARGUMENT_EXTRA_OCCURRENCE')


def snapshot_target(session, lane, target, kind):
    if session:
        fragment = target.custody_fragment
        validate_fragment(fragment, session, live=False)
        lane_data = session.lanes.setdefault(lane,{})
        if kind == 'PYZ':
            expected = session.analysis[lane]['pure']
            actual = fragment_event(fragment,'pyz_candidates')
            need(not match_prior_rows(expected,actual,'PYZ'), 'PYZ_EXTRA_PRIOR_OCCURRENCE')
        else:
            check_exe_arguments(lane_data['EXE_arguments'],fragment_event(fragment,'exe_arguments'))
            raw = fragment_event(fragment,'normalization')['raw']
            expected = lane_data['PYZ_dependencies'] + session.analysis[lane]['scripts']
            match_prior_rows(expected,raw,'EXE')
            envelope = [row for row in raw if row['kind'] == 'PYZ']
            need(len(envelope) == 1 and (envelope[0]['subject']['size'],envelope[0]['subject']['sha256'])
                 == (lane_data['PYZ_output']['size'],lane_data['PYZ_output']['sha256']), 'EXE_PYZ_GENERATION')
        lane_data[kind] = fragment
        lane_data[kind+'_output'] = session.snapshot(target.name,lane,kind)
        lane_data[kind+'_dependencies'] = session.rows(target.dependencies,lane,kind+'_dependencies')
        if kind == 'EXE':
            lane_data['exefiles'] = session.rows(target.exefiles,lane,'exefiles')
            lane_data['pkg'] = session.snapshot(target.pkg.name,lane,'PKG')
            lane_data['pkg_toc'] = [list(row) for row in target.pkg.toc]
            lane_data['python_library'] = target.pkg.python_lib_name


def spec_collect_inputs(session, exe, exe_cli, shared):
    if session is None:
        return None
    raw, actual = [], []
    for lane, target in (('daemon',exe),('cli',exe_cli)):
        raw += session.rows([(Path(target.name).name,target.name,'EXECUTABLE')],lane,'collect_exe')
        raw += session.lanes[lane]['EXE_dependencies']
        actual += [[Path(target.name).name,target.name,'EXECUTABLE'], *[list(r) for r in target.dependencies]]
    actual += [list(row) for row in shared]
    for lane in ('daemon','cli'):
        raw += session.analysis[lane]['binaries'] + session.analysis[lane]['datas']
    prepare_collect_ledger(raw)
    for row in raw:
        if row['subject']:
            check_live_generation(row['subject'],session)
    session.inputs = session.write('inputs', {'toolchain':session.toolchain, 'installed':session.installed,
        'roots':session.roots, 'analysis':session.analysis,'lanes':session.lanes,
        'attribution':attribute_sources(session), 'aliases':session.aliases,
        'contributors':raw,'collect_arguments':actual,
        'unresolved':['compilation_origin_unobserved','interpreter_installer_custody_unobserved','terms_applicability_unresolved']},
        [session.reference('context',session.context_sha256)])
    return session.custody('shared','COLLECT',session.inputs)



def check_alias(value,session):
    keys(value, {'root','original','terminal','aliases'}, 'ALIAS_SCHEMA')
    need(value['root'] in session.roots, 'ALIAS_ROOT')
    terminal,aliases = resolve_link(value['root'],value['original'],session.budget)
    need(terminal.relative_to(value['root']).as_posix() == value['terminal'] and aliases == value['aliases'], 'ALIAS_GENERATION_CHANGED')


def validate_native(operation, session, prior=True):
    keys(operation, {'input','returned','options','cache','outcome','predecessor','path','destination'}, 'NATIVE_OPERATION_SCHEMA')
    check_operation_options(operation['options'], 'process_entry')
    if operation['options'].get('entitlements') is not None:
        check_subject(operation['options']['entitlements'],session.roots,session.budget)
    need(operation['cache'] in ('created','cache_hit','unchanged') and operation['outcome'] in
         ('completed','non_macho_passthrough','optional_arch_passthrough'), 'NATIVE_OUTCOME')
    need(not (operation['outcome'] == 'optional_arch_passthrough' and operation['options']['strict_arch_validation']), 'STRICT_ARCH_OUTCOME')
    for key in ('input','returned'):
        need(operation[key]['snapshot'] is not None and absolute(operation[key]['snapshot']).is_relative_to(session.stage), 'NATIVE_GENERATION_RUN')
        check_subject(operation[key],session.roots,session.budget,live=False)
    need(str(Path(operation['returned']['root']) / operation['returned']['relative']) == operation['path']
         and operation['destination'] == operation['options']['destination'], 'NATIVE_PATH_BINDING')
    if operation['cache'] == 'cache_hit':
        predecessor = operation['predecessor']
        need(prior and type(predecessor) is dict, 'CACHE_PREDECESSOR_REQUIRED')
        validate_native(predecessor,session,False)
        need(option_identity(predecessor['options']) == option_identity(operation['options']) and predecessor['destination'] == operation['destination']
             and predecessor['outcome'] == operation['outcome'], 'CACHE_OPTIONS_OR_OUTCOME')
        for key in ('input','returned'):
            need((predecessor[key]['size'],predecessor[key]['sha256']) == (operation[key]['size'],operation[key]['sha256']), 'CACHE_GENERATION')
    else:
        need(operation['predecessor'] is None, 'UNEXPECTED_PREDECESSOR')


def validate_fragment(fragment, session, live=False, native_index=None):
    fields = {'encoding','run_id','stage_dir','source_roots','lane','target','ordinal','ledger_ref','events'}
    keys(fragment, fields, 'FRAGMENT_SCHEMA')
    need(fragment['encoding'] == 'happyranch-freezer-fragment@1' and fragment['run_id'] == session.run
         and fragment['lane'] in ('daemon','cli','shared') and fragment['target'] in ('PYZ','EXE','COLLECT')
         and type(fragment['ordinal']) is int and fragment['ordinal'] == 0, 'FRAGMENT_BINDING')
    stage = session.stage / fragment['target'] / fragment['lane']
    if native_index is not None:
        stage = stage / str(native_index)
    retained,_ = read(stage/'fragment.json',session.budget,RECORD_CAP,_json=True)
    need(retained == encoded(fragment), 'RETAINED_FRAGMENT_MISMATCH')
    need(fragment['stage_dir'] == str(stage) and fragment['source_roots'] == session.roots, 'FRAGMENT_ROOT_BINDING')
    need((fragment['ledger_ref'] is None) == (fragment['target'] != 'COLLECT'), 'FRAGMENT_DAG')
    need(type(fragment['events']) is list and len(fragment['events']) <= MAX_MEMBERS, 'FRAGMENT_EVENTS_CAP')
    copy_index = 0
    for ordinal,event in enumerate(fragment['events']):
        keys(event, {'ordinal','kind','payload'}, 'EVENT_SCHEMA')
        need(type(event['ordinal']) is int and event['ordinal'] == ordinal, 'EVENT_ORDINAL')
        kind, value = event['kind'], event['payload']
        if kind == 'source_alias':
            check_alias(value,session)
        elif kind == 'options':
            check_operation_options(value)
        elif kind in ('bootstrap_selected','bootstrap_generated','pyz_candidates'):
            for row in value:
                row_shape(row)
                if row['subject']:
                    check_subject(row['subject'],session.roots,session.budget,live=False)
        elif kind == 'bootstrap_redirect':
            keys(value, {'name','contributors','selected','generated','origin'}, 'REDIRECT_SCHEMA')
            need(value['origin'] == 'declared_compile_origin' and value['contributors'] and value['generated'], 'REDIRECT_INCOMPLETE')
            row_shape(value['selected'])
        elif kind == 'exe_arguments':
            for i,arg in enumerate(value):
                keys(arg, {'ordinal','kind','rows'}, 'EXE_ARGUMENT_SCHEMA')
                need(type(arg['ordinal']) is int and arg['ordinal'] == i and arg['kind'] in ('PYZ','TOC'), 'EXE_ARGUMENT_IDENTITY')
                for row in arg['rows']:
                    row_shape(row)
                    if row['subject']:
                        check_subject(row['subject'],session.roots,session.budget,live=False)
        elif kind == 'normalization':
            keys(value, {'raw','normalized','edges'}, 'NORMALIZATION_SCHEMA')
            check_normalization_edges(value['raw'],value['normalized'],value['edges'],fragment['target'])
            for row in value['raw']:
                if row['subject']:
                    check_subject(row['subject'],session.roots,session.budget,live=False)
        elif kind == 'pyz_skipped':
            keys(value, {'name','source','reason'}, 'PYZ_SKIP_SCHEMA')
            need(value['reason'] == 'SyntaxError', 'PYZ_SKIP_REASON')
        elif kind == 'pyz_output':
            keys(value, {'subject','emitted','origin'}, 'PYZ_OUTPUT_SCHEMA')
            need(value['origin'] == 'declared_compile_origin', 'COMPILE_ORIGIN')
            check_subject(value['subject'],session.roots,session.budget,live=False)
        elif kind == 'exe_operation':
            keys(value, {'selected','copied','pkg','append_input','before_append','after_append','output','target_arch',
                         'branch','append_pkg','origin','outcome','architecture_output','arch_action'}, 'EXE_OPERATION_SCHEMA')
            need(value['append_pkg'] is True and value['outcome'] == 'completed' and value['origin'] == 'origin_unobserved'
                 and value['branch'] == 'darwin_binary_to_target_arch' and value['target_arch'] == 'arm64', 'EXE_OUTCOME')
            for key in ('selected','copied','pkg','append_input','before_append','after_append','output','architecture_output'):
                check_subject(value[key],session.roots,session.budget,live=False)
            need(value['arch_action'] in ('narrowed','passthrough'), 'EXE_ARCH_ACTION')
            with container_subject(value['copied'],session) as raw:
                fat = raw[:4].hex() in ('cafebabe','bebafeca','cafebabf','bfbafeca')
                need(value['arch_action'] == ('narrowed' if fat else 'passthrough'), 'EXE_ARCH_BRANCH')
            with container_subject(value['architecture_output'],session) as raw:
                need(raw[:4].hex() in ('feedface','cefaedfe','feedfacf','cffaedfe'), 'EXE_ARCH_OUTPUT')
            if value['arch_action'] == 'passthrough':
                need(value['architecture_output']['sha256'] == value['copied']['sha256'], 'EXE_ARCH_PASSTHROUGH')
            need(value['after_append']['size'] == value['before_append']['size'] + value['append_input']['size'], 'EXE_APPEND_LENGTH')
            with container_subject(value['after_append'],session) as raw:
                split = value['before_append']['size']
                need(range_digest(raw,0,split,session.budget) == value['before_append']['sha256']
                     and range_digest(raw,split,len(raw),session.budget) == value['append_input']['sha256'], 'EXE_APPEND_RANGES')
            need((value['selected']['size'],value['selected']['sha256']) == (value['copied']['size'],value['copied']['sha256'])
                 and value['pkg']['sha256'] == value['append_input']['sha256'], 'EXE_COPY_OR_APPEND_BINDING')
        elif kind == 'native_return':
            validate_native(value,session)
        elif kind == 'collect_copy':
            keys(value, {'destination','source','kind','physical','copy','native'}, 'COLLECT_COPY_SCHEMA')
            keys(value['copy'], {'input','output','literal'}, 'COPY_SCHEMA')
            if value['kind'] == 'SYMLINK':
                need(value['copy']['literal'] == value['source'] and value['copy']['input'] is None
                     and value['copy']['output'] is None and value['native'] is None, 'COPY_LINK')
            else:
                for key in ('input','output'):
                    check_subject(value['copy'][key],session.roots,session.budget,live=False)
                need(all(value['copy']['input'][k] == value['copy']['output'][k] for k in ('size','sha256')), 'COPY_GENERATION')
                if value['kind'] in ('BINARY','EXTENSION'):
                    validate_native(value['native'],session)
                    process = parse(read(stage/str(copy_index)/'fragment.json',session.budget,RECORD_CAP,_json=True)[0])
                    validate_fragment(process,session,native_index=copy_index)
                    need(fragment_event(process,'native_return') == value['native'], 'PROCESS_FRAGMENT_BINDING')
                    need(all(value['native']['returned'][k] == value['copy']['input'][k] for k in ('size','sha256','identity')),
                         'NATIVE_RETURN_NOT_CONSUMED')
                else:
                    need(value['native'] is None, 'UNEXPECTED_NATIVE_OPERATION')
            copy_index += 1
        else:
            raise FreezerError('FREEZER_EVENT_KIND')


def fragment_event(fragment, kind):
    values = [event['payload'] for event in fragment['events'] if event['kind'] == kind]
    need(len(values) == 1, 'REQUIRED_EVENT:' + kind)
    return values[0]


def archive_observations(session, inputs):
    tool = inputs['toolchain']
    magic = bytes.fromhex(tool['bytecode_magic'])
    result = {}
    for lane in ('daemon','cli'):
        info = inputs['lanes'][lane]
        exe = fragment_event(info['EXE'],'exe_operation')
        provenance = parse(read(session.paths.exported_source/VENDOR/'provenance.json',session.budget,256*1024,_json=True)[0])
        selected_member = 'PyInstaller/bootloader/Darwin-64bit/run'
        original_bootloader = provenance['original_members'][selected_member]
        need((exe['selected']['size'],exe['selected']['sha256']) == (original_bootloader['size'],original_bootloader['sha256']), 'BOOTLOADER_ORIGINAL_BINDING')
        pkg = info['pkg']
        need(exe['pkg']['sha256'] == pkg['sha256'] and exe['output']['sha256'] == info['EXE_output']['sha256'], 'LANE_OPERATION_BINDING')
        pyz = info['PYZ_output']
        with container_subject(pkg,session) as raw_pkg:
            pkg_map = pkg_members(raw_pkg,magic,tool['python_version'],session.budget,pkg['sha256'])
            need(pkg_map['start'] == 0 and pkg_map['end'] == len(raw_pkg), 'PKG_COMPLETE_RANGE')
        with container_subject(exe['output'],session) as raw_exe:
            exe_map = pkg_members(raw_exe,magic,tool['python_version'],session.budget,pkg['sha256'])
        need(pkg_map['members'] == exe_map['members'] and pkg_map['python_library'] == info['python_library'], 'EXE_PKG_BINDING')
        with container_subject(pyz,session) as raw_pyz:
            pyz_map = pyz_members(raw_pyz,magic,session.budget)
        embedded = [m for m in pkg_map['members'] if m['kind'] == 'z']
        need(len(embedded) == 1 and embedded[0]['sha256'] == pyz['sha256'] and embedded[0]['pyz'] == pyz_map, 'LANE_PYZ_BINDING')
        emitted = fragment_event(info['PYZ'],'pyz_output')['emitted']
        need([m['name'] for m in pyz_map] == [entry[0] for entry in emitted], 'PYZ_EMITTED_MEMBERSHIP')
        normalization = fragment_event(info['PYZ'],'normalization')
        skipped = [e['payload']['name'] for e in info['PYZ']['events'] if e['kind'] == 'pyz_skipped']
        need(len(set(skipped)) == len(skipped) and sorted([m['name'] for m in pyz_map]+skipped)
             == sorted(entry[0] for entry in normalization['normalized']), 'PYZ_UNEXPLAINED_OMISSION')
        bootstrap_expected, archive_expected = [], []
        conversion = {'PYMODULE':'m','PYMODULE-1':'m','PYMODULE-2':'m','PYSOURCE':'s','PYSOURCE-1':'s','PYSOURCE-2':'s',
                      'PYZ':'z','OPTION':'o'}
        for dest,source,kind in info['pkg_toc']:
            if kind not in ('BINARY','EXTENSION','DATA','ZIPFILE'):
                need(kind in conversion, 'PKG_UNSUPPORTED_CLASS')
                converted = ['PYZ.pyz' if kind == 'PYZ' else dest, conversion[kind]]
                (bootstrap_expected if kind.startswith(('PYMODULE','PYSOURCE')) else archive_expected).append(converted)
        expected = bootstrap_expected + sorted(archive_expected,key=lambda row:(row[1],row[0]))
        need([[m['name'],m['kind']] for m in pkg_map['members']] == expected, 'PKG_DECLARED_MEMBERSHIP')
        zips = []
        for kind in ('binaries','datas'):
            for row in inputs['analysis'][lane][kind]:
                if row['destination'] == 'base_library.zip':
                    with container_subject(row['subject'],session) as raw:
                        zips.append({'subject':row['subject'],'members':zip_members(raw,magic,session.budget)})
        need(len(zips) == 1, 'BASE_ZIP_GENERATION_REQUIRED')
        result[lane] = {'pkg':pkg_map,'embedded':exe_map,'pyz':pyz_map,'base_zip':zips}
    return result


def reconcile_collect(session, inputs, fragment, census, inputs_ref):
    validate_fragment(fragment,session)
    need(fragment['ledger_ref'] == inputs_ref, 'COLLECT_LEDGER_DAG')
    normalization = fragment_event(fragment,'normalization')
    check_collect_expansion(inputs['collect_arguments'], [[r['destination'],r['source'],r['kind']] for r in normalization['raw']])
    groups = groups_for(inputs['contributors'])
    operations = [e['payload'] for e in fragment['events'] if e['kind'] == 'collect_copy']
    need(len(operations) == len(normalization['normalized']) == len(groups), 'COLLECT_OPERATION_COUNT')
    physical_names([v[0] for v in groups.values()])
    files = {row['relative']:row for row in census if row['kind'] != 'directory'}
    edges = []
    for operation in operations:
        members = groups.pop(os.path.normpath(operation['destination']), [])
        need(members and operation['kind'] == members[0]['kind'], 'NORMALIZATION_LOSS')
        expected = operation['destination'] if operation['kind'] in ('EXECUTABLE','PKG') else '_internal/'+operation['destination']
        need(expected == operation['physical'] and expected in files, 'COLLECT_PHYSICAL_MAPPING')
        physical = files.pop(expected)
        if operation['kind'] == 'SYMLINK':
            need(physical['kind'] == 'link' and physical['target'] == operation['copy']['literal'], 'COLLECT_LINK_BINDING')
        else:
            result = operation['copy']['output']
            need(physical['kind'] == 'file' and (physical['size'],physical['sha256']) == (result['size'],result['sha256']), 'COLLECT_PHYSICAL_BYTES')
            consumed = operation['native']['input'] if operation['native'] else operation['copy']['input']
            need(all(r['subject'] and (r['subject']['size'],r['subject']['sha256']) == (consumed['size'],consumed['sha256'])
                     for r in members), 'COLLECT_CONTRIBUTOR_BINDING')
        edges.append({'physical':expected,'contributors':[r['id'] for r in members]})
    need(not groups and not files, 'COLLECT_UNEXPLAINED_OUTPUT')
    need(sum(len(e['contributors']) for e in edges) == len(inputs['contributors']), 'NORMALIZATION_LOSS')
    return edges


def finish_freeze(session, collect):
    if session is None:
        return
    try:
        inputs = session.load('inputs',session.inputs['sha256'])['payload']
        dist = session.paths.output / 'scratch/freeze-dist/happyranch-daemon'
        census = tree(dist,session.budget)
        edges = reconcile_collect(session,inputs,collect.custody_fragment,census,session.inputs)
        archives = archive_observations(session,inputs)
        output = session.write('output', {'collect_fragment':collect.custody_fragment,'collect_census':census,
            'archives':archives,'inputs':session.inputs,'contributor_edges':edges,'unresolved':inputs['unresolved']},[session.inputs])
        create(session.stage/'return/token.json',encoded({'run_id':session.run,'context':session.context_sha256,
                                                        'inputs':session.inputs['sha256'],'output':output['sha256']}), session.budget)
    except BaseException as error:
        session.fail(error)
        raise


def check_inputs(session, inputs):
    keys(inputs['analysis'], {'daemon','cli'}, 'ANALYSIS_LANES')
    keys(inputs['lanes'], {'daemon','cli'}, 'TARGET_LANES')
    need(type(inputs['roots']) is list and len(inputs['roots']) <= 16 and inputs['roots'][:2] == session.roots[:2], 'INPUT_ROOTS')
    # Re-admit each root physically; records never confer an arbitrary regular-read exemption.
    for root in inputs['roots']:
        with ASSET['RootHandle'](root,'/',False):
            pass
    session.roots = inputs['roots']
    keys(inputs['toolchain'], {'executable','version','implementation','machine','base_prefix','stdlib','bytecode_magic','python_version','origin','receipt','python_executable','python_alias','native_tools'}, 'TOOLCHAIN_SCHEMA')
    need(inputs['toolchain']['origin'] == 'origin_unobserved' and inputs['toolchain']['machine'] == 'arm64', 'TOOLCHAIN_ORIGIN')
    check_toolchain(session,inputs['toolchain'])
    for lane in ('daemon','cli'):
        analysis = inputs['analysis'][lane]
        keys(analysis, {'scripts','pure','binaries','datas'}, 'ANALYSIS_SCHEMA')
        info = inputs['lanes'][lane]
        keys(info, {'PYZ','PYZ_output','PYZ_dependencies','EXE','EXE_output','EXE_dependencies','EXE_arguments','exefiles','pkg','pkg_toc','python_library'}, 'LANE_SCHEMA')
        need(not match_prior_rows(analysis['pure'],fragment_event(info['PYZ'],'pyz_candidates'),'PYZ'), 'PYZ_EXTRA_PRIOR_OCCURRENCE')
        check_exe_arguments(info['EXE_arguments'],fragment_event(info['EXE'],'exe_arguments'))
        match_prior_rows(info['PYZ_dependencies']+analysis['scripts'],fragment_event(info['EXE'],'normalization')['raw'],'EXE')
        for kind in ('PYZ','EXE'):
            validate_fragment(info[kind],session)
            check_subject(info[kind+'_output'],session.roots,session.budget,live=False)
        for rows in (*analysis.values(),info['PYZ_dependencies'],info['EXE_dependencies'],info['exefiles']):
            for row in rows:
                row_shape(row)
                if row['subject']:
                    check_subject(row['subject'],session.roots,session.budget,live=False)
        check_subject(info['pkg'],session.roots,session.budget,live=False)
    for alias in inputs['aliases']:
        check_alias(alias,session)
    validate_attribution(session,inputs['attribution'])
    prepare_collect_ledger(inputs['contributors'])
    for row in inputs['installed']:
        keys(row, {'member','subject'}, 'INSTALLED_SCHEMA')
        check_subject(row['subject'],session.roots,session.budget)


def verify_freezer(candidate, paths, run, context, inputs_sha, output_sha, copy_sha=None, *, _budget=None):
    session = Session(candidate,paths,run,context,_budget=_budget)
    session.context()
    inputs_row = session.load('inputs',inputs_sha)
    need(inputs_row['upstream'] == [session.reference('context',context)], 'INPUTS_DAG')
    check_inputs(session,inputs_row['payload'])
    output_row = session.load('output',output_sha)
    need(output_row['payload']['inputs'] == session.reference('inputs',inputs_sha) and output_row['upstream'] == [output_row['payload']['inputs']], 'OUTPUT_DAG')
    dist = paths.output / 'scratch/freeze-dist/happyranch-daemon'
    current = tree(dist,session.budget)
    need(current == output_row['payload']['collect_census'], 'DIST_GENERATION_CHANGED')
    edges = reconcile_collect(session,inputs_row['payload'],output_row['payload']['collect_fragment'],current,session.reference('inputs',inputs_sha))
    need(edges == output_row['payload']['contributor_edges'], 'CONTRIBUTOR_EDGE_BINDING')
    need(archive_observations(session,inputs_row['payload']) == output_row['payload']['archives'], 'ARCHIVE_REOPEN_BINDING')
    if copy_sha:
        copy = session.load('copy',copy_sha)
        keys(copy['payload']['output'], {'path','size','sha256'}, 'COPY_OUTPUT_REF')
        need(copy['payload']['output'] == session.reference('output',output_sha) and copy['upstream'] == [copy['payload']['output']], 'COPY_DAG')
        app = tree(paths.app/'Contents/Resources/daemon',session.budget)
        need(copy['payload']['before'] == current and copy['payload']['after'] == app, 'APP_GENERATION_CHANGED')
        need(compare_copy(current,app,allowed_modes(paths)) == copy['payload']['mode_changes'], 'MODE_CHANGE_BINDING')
        need(copy['payload']['unresolved'] == output_row['payload']['unresolved'] == inputs_row['payload']['unresolved'], 'REFUSAL_NOT_MONOTONIC')
    return session, output_row


def allowed_modes(paths):
    # The existing wrapper restores exactly tracked skills' modes after cp.
    # Both bytes and type are still compared for every entry.
    roots = [paths.exported_source/'runtime/skills/bundled',paths.exported_source/'skills']
    roots += [paths.exported_source/'runtime/skills'/slug for slug in ('manage-agent','manage-repo','reflection')]
    result = {}
    candidates = [paths.exported_source/'runtime/skills/README.md']
    for root in roots:
        with ASSET['RootHandle'](root,'/',True):
            pass
        candidates.extend(root.rglob('*'))
    need(len(candidates) <= 8192, 'MODE_LEDGER_CAP')
    for path in candidates:
        info = path.lstat()
        if stat.S_ISREG(info.st_mode):
            result['_internal/'+path.relative_to(paths.exported_source).as_posix()] = stat.S_IMODE(info.st_mode)
    return result


def verify_arguments(args):
    candidate,paths = ASSET['invocation'](args)
    return verify_freezer(candidate,paths,args.run_id,args.freezer_context_sha256,args.freezer_inputs_sha256,
                          args.freezer_output_sha256,args.freezer_copy_sha256)


def add_arguments(parser):
    for key in ('freezer_context_sha256','freezer_inputs_sha256','freezer_output_sha256','freezer_copy_sha256'):
        parser.add_argument('--'+key.replace('_','-'),required=True,action=ASSET['Once'])


def main():
    parser = argparse.ArgumentParser(allow_abbrev=False)
    sub = parser.add_subparsers(dest='command',required=True)
    q = sub.add_parser('begin',allow_abbrev=False)
    ASSET['add_arguments'](q)
    for key in ('asset-context-sha256','toolchains-sha256'):
        q.add_argument('--'+key,required=True,action=ASSET['Once'])
    for command in ('accept','before-copy','copy','verify','fail'):
        q = sub.add_parser(command,allow_abbrev=False)
        ASSET['add_arguments'](q)
        q.add_argument('--freezer-context-sha256',required=True,action=ASSET['Once'])
        if command in ('before-copy','copy','verify'):
            for key in ('inputs','output'):
                q.add_argument('--freezer-'+key+'-sha256',required=True,action=ASSET['Once'])
        if command == 'verify':
            q.add_argument('--freezer-copy-sha256',required=True,action=ASSET['Once'])
        if command == 'fail':
            q.add_argument('--original-exit',type=int,required=True)
    q = sub.add_parser('token-field',allow_abbrev=False)
    q.add_argument('--token',required=True); q.add_argument('--field',required=True)
    args = parser.parse_args()
    if args.command == 'token-field':
        budget = Budget()
        budget.charge(len(args.token.encode()), 'records')
        value = parse(args.token.encode())
        need(args.field in value,'TOKEN_FIELD')
        result = value[args.field]
        if isinstance(result,dict):
            raw = encoded(result)
            budget.charge(len(raw), 'records')
            result = raw.decode().strip()
        print(result)
        return
    candidate,paths = ASSET['invocation'](args)
    session = Session(candidate,paths,args.run_id,getattr(args,'freezer_context_sha256',None))
    try:
        if args.command == 'begin':
            result = begin(candidate,paths,args.run_id,args.asset_context_sha256,args.toolchains_sha256,_budget=session.budget)
        elif args.command == 'fail':
            session.fail('wrapper_command_failure',args.original_exit)
            return
        elif args.command == 'accept':
            session.context()
            raw,_ = read(session.stage/'return/token.json',session.budget,4096,_json=True)
            result = parse(raw)
            keys(result, {'run_id','context','inputs','output'}, 'RETURN_TOKEN_SCHEMA')
            need(result['run_id'] == args.run_id and result['context'] == args.freezer_context_sha256, 'RETURN_TOKEN_BINDING')
            verify_freezer(candidate,paths,args.run_id,result['context'],result['inputs'],result['output'],_budget=session.budget)
            create(session.stage/'return/consumed.json',raw,session.budget)
        else:
            session, output = verify_freezer(candidate,paths,args.run_id,args.freezer_context_sha256,
                args.freezer_inputs_sha256,args.freezer_output_sha256,getattr(args,'freezer_copy_sha256',None),_budget=session.budget)
            if args.command == 'before-copy':
                create(session.stage/'return/copy-ready.json',encoded({'run_id':args.run_id,'output':args.freezer_output_sha256}),session.budget)
                result = {'ready':True}
            elif args.command == 'copy':
                ready = parse(read(session.stage/'return/copy-ready.json',session.budget,4096,_json=True)[0])
                need(ready == {'run_id':args.run_id,'output':args.freezer_output_sha256}, 'COPY_READY_BINDING')
                before = tree(paths.output/'scratch/freeze-dist/happyranch-daemon',session.budget)
                after = tree(paths.app/'Contents/Resources/daemon',session.budget)
                changes = compare_copy(before,after,allowed_modes(paths))
                ref = session.reference('output',args.freezer_output_sha256)
                result = session.write('copy',{'output':ref,'before':before,'after':after,'mode_changes':changes,
                                              'unresolved':output['payload']['unresolved']},[ref])
            else:
                result = {'technical_relations':'MATCHED','notice_standard':'dependency-manifest-notices'}
        raw = encoded(result)
        need(len(raw) <= 4096,'TOKEN_CAP')
        session.budget.charge(len(raw), 'records')
        print(raw.decode(),end='')
    except BaseException as error:
        session.fail(error)
        raise


if __name__ == '__main__':
    main()
