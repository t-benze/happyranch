#!/usr/bin/env python3
"""Guided human-host coding setup; admission stays Disabled.

No startup migration, automatic rollback, import replay or maintenance lock.
Run from the published checkout in a genuine human terminal. Missing ownership
or serving observations are actionable holds, never successful setup.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import io
import json
import marshal
import os
import re
import secrets
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import sysconfig
import tarfile
import time
import tomllib
import zipfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from urllib.parse import quote, urlsplit

_MARKERS = ('HAPPYRANCH_RUNTIME_SESSION_ID', 'HAPPYRANCH_ORG_SLUG',
            'HAPPYRANCH_TASK_TMP_ROOT', 'HAPPYRANCH_TASK_SCRATCH_MANIFEST')
_COMPATIBLE = '3ce8f69983244eaa4df1c7aface1301b784941d4'
_SOURCE = Path(os.path.abspath(__file__)).parent.parent
_LIMIT = 1024 * 1024


class HostRefusal(ValueError):
    """A safe host-entry diagnostic with no private product payload."""


class ArgumentRefusal(ValueError):
    """Parser exit2 without echoing untrusted argv or parser messages."""


class SetupParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentRefusal()


class DiagnosticContext:
    """Invocation-local facts, not a recovery cursor or settlement authority.

    This exists before the host guard and survives a partial constructor.
    Exception bodies, argv, environment and transport bodies are never rendered.
    """
    def __init__(self) -> None:
        self.incident = 'coding-setup-' + secrets.token_hex(8)
        self.stage = 'host-entry'
        self.observation = 'host-entry'
        self.last_completed = 'none'
        self.layout = 'not-yet-accepted'
        self.effects = {name: 'not-attempted' for name in (
            'receipt-custody', 'stop', 'backup', 'preparation-uv', 'preparation-npm', 'preparation-ui',
            'migration-G', 'migration-H', 'migration-I', 'migration-J', 'start', 'start-pair', 'serving',
            'import-file', 'import-intent', 'import-send', 'import-receipt', 'import-readback')}
        self.pre_effect = True
        self.receipts = None
        self.references = {}
        self.paths = {}
        self.preparations = {}
        self.observations = {}
        self.checks = {}
        self.command = None
        self.secondary = []
        self.reported = False

    def enter(self, stage: str) -> None:
        self.stage = stage
        self.observation = stage

    def observe(self, observation: str) -> None:
        self.observation = observation

    def accepted(self, stage: str) -> None:
        self.last_completed = stage

    def effect(self, name: str, state: str) -> None:
        self.pre_effect = False
        self.effects[name] = state

    @staticmethod
    def token(value: object) -> str:
        return value if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value) else 'unavailable'

    def report(self, exc: BaseException) -> None:
        if self.reported:
            return
        self.reported = True
        category = ('arguments_invalid' if isinstance(exc, ArgumentRefusal) else
                    'host_entry_refused' if isinstance(exc, HostRefusal) else
                    'interrupted' if isinstance(exc, KeyboardInterrupt) else 'observation_or_effect_failed')
        allowed = {
            'observation_hold', 'original_owner_unsettled', 'reserved_relocation_custody', 'reserved_archive_custody',
            'native_preparation_closure_unavailable', 'native_started_closure_unavailable',
            'preparation_list_invalid', 'preparations_owned', 'serving_observation_unavailable', 'command_failed',
            'current_owner_active',
        }
        hold_type = globals().get('Hold')
        if hold_type is not None and isinstance(exc, hold_type) and exc.category in allowed:
            category = exc.category
        owner_references = {}
        if hold_type is not None and isinstance(exc, hold_type) and isinstance(exc.details, dict):
            for key in ('org', 'table', 'task_id', 'session_id', 'invocation_id', 'audit_id', 'path'):
                value = exc.details.get(key)
                if key == 'audit_id' and type(value) is int and value > 0:
                    owner_references[key] = value
                elif key == 'path':
                    if (isinstance(value, str) and len(value) <= 4096 and value.startswith('/')
                            and all(ord(c) >= 32 for c in value) and '..' not in Path(value).parts):
                        owner_references[key] = value  # JSON escaping below, never shell prose
                elif isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value):
                    owner_references[key] = value
            owner = exc.details.get('owner')
            if isinstance(owner, dict):
                for key in ('id', 'task_id', 'root_task_id', 'record_id', 'source_id', 'session_id',
                            'current_session_id', 'preparation_id', 'operation_id', 'writer_id', 'invocation_id'):
                    value = owner.get(key)
                    if (type(value) is int and value > 0
                            or isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', value)):
                        owner_references['owner.' + key] = value
        locations = []
        tb = exc.__traceback__
        while tb is not None:
            path = Path(tb.tb_frame.f_code.co_filename)
            if path.is_absolute() and path.is_relative_to(_SOURCE):
                relative = str(path.relative_to(_SOURCE))
                if re.fullmatch(r'[A-Za-z0-9_./-]{1,256}', relative):
                    locations.append(f'{relative}:{self.token(tb.tb_frame.f_code.co_name)}:{tb.tb_lineno}')
            tb = tb.tb_next
        facts = {
            'incident': self.incident, 'category': category, 'exception_class': self.token(type(exc).__name__),
            'stage': self.stage, 'last_completed': self.last_completed, 'last_accepted_layout': self.layout,
            'observation': self.observation,
            'effects': dict(self.effects), 'current_command': self.command, 'locations': locations[-8:],
            'secondary_failures': self.secondary[:],
            'serving_checks': self.checks,
            'owner_references': owner_references,
            'candidate_or_observed_paths': self.paths,
        }
        detail = 'detail exists only in this output; no private diagnostic file saved'
        saved = None
        if self.receipts is not None:
            try:
                # Only structured owner/observation facts go into private detail;
                # never serialize arbitrary exception text or traceback locals.
                private = dict(facts, references=self.references, active_preparations=self.preparations,
                               observations=self.observations,
                               hold_details=exc.details if hold_type is not None and isinstance(exc, hold_type) else None)
                raw = canonical(private)
                if len(raw) > _LIMIT:
                    raise ValueError('diagnostic_size_limit')
                path = self.receipts / (self.incident + '.json')
                exclusive(path, raw)
                saved = str(path)
                detail = 'private diagnostic saved: ' + json.dumps(saved, ensure_ascii=True)
            except BaseException as persistence:
                facts['diagnostic_persistence_failed'] = self.token(type(persistence).__name__)
                detail += '; private diagnostic persistence failed; any partial file is incomplete'
        guidance = {
            'preparation_list_invalid': 'Engineering/serving observer owns the malformed or unavailable complete current preparation list.',
            'preparations_owned': 'Engineering/operator must exclude current preparation owners; original references remain private.',
            'serving_observation_unavailable': 'Engineering/serving owner must establish supported complete running-entry observations, or the human operator must establish positive already-stopped/excluded entry through existing controls. Failed opening lists grant no exclusion.',
        }.get(category, 'Engineering inspects the current stage and original causes. Complete present exclusion and verified backup are required; historical uncertainty alone needs no closure certification. Failed or uncertain effects prevent the next effect.')
        public = dict(facts)
        # Original references/observation bodies stay in memory/private custody.
        # Only a fixed-shape command summary is suitable for terminal output.
        public['current_command'] = self.command
        unchanged = 'Nothing was changed. ' if self.pre_effect else 'Change-free status is not established. '
        message = ('Setup held: ' + json.dumps(public, sort_keys=True, ensure_ascii=True) + '\n'
                   + unchanged + detail + '\n' + guidance
                   + '\nPreserve original intents/results/keys/bytes; no automatic restart/restore/replay.'
                   + '\nSend this output to engineering_manager')
        raw = message.encode('utf8')
        try:
            written = os.write(2, raw)
            if written != len(raw):
                # Finish the undelivered suffix once, without repeating a
                # possibly delivered instruction or recursively reporting.
                if os.write(2, raw[written:]) != len(raw) - written:
                    raise OSError('report_output_short_write')
        except BaseException:
            fallback = (f'Setup held incident={self.incident} stage={self.stage} category={category} '
                        f'last_completed={self.last_completed} layout={self.layout} '
                        f'effects={json.dumps(self.effects, sort_keys=True)}; reporting_failed; '
                        + detail + '\nSend this output to engineering_manager')
            try:
                os.write(2, fallback.encode('utf8'))
            except BaseException:
                pass  # No delivery/persistence guarantee after all sinks fail.


def require_human_host() -> None:
    """Stdlib only; run before even importing the product preflight helper."""
    if any(key in os.environ for key in _MARKERS):
        raise HostRefusal('managed invocation marker present; run from a genuine human host terminal after agent work ends')
    for path in (Path.cwd(), _SOURCE):
        parts = path.parts
        if 'workspaces' in parts or '.claude' in parts or '.happyranch/task-tmp' in str(path):
            raise HostRefusal('registered/managed workspace execution refused; use the published human host checkout')
    for path in (_SOURCE, Path(os.path.abspath(__file__))):
        for part in reversed((path, *path.parents)):
            if stat.S_ISLNK(part.lstat().st_mode):
                raise HostRefusal('literal source path required; aliased source/distribution refused')
    # Do not turn missing platform attribution into a human assertion. Linux
    # exposes checked original parent/start/executable identities before imports.
    if sys.platform != 'linux':
        raise HostRefusal('host ancestry observation unavailable on this platform; original platform observer owner must supply supported attribution')
    pid, visited = os.getpid(), set()
    while pid >= 1:
        if pid in visited or len(visited) >= 128:
            raise HostRefusal('host ancestry incomplete')
        visited.add(pid)
        proc = Path('/proc') / str(pid)
        before = (proc / 'stat').read_text()
        fields = before[before.rfind(')') + 2:].split()
        if len(fields) < 20:
            raise HostRefusal('host process start/parent identity unavailable')
        executable = os.readlink(proc / 'exe')
        cwd = os.readlink(proc / 'cwd')
        command = (proc / 'cmdline').read_bytes()
        if (b'runtime.daemon' in command or b'jobs_runner' in command
                or b'task_native' in command or '/workspaces/' in cwd
                or '/.happyranch/task-tmp/' in cwd or not executable):
            raise HostRefusal('daemon/task/job/native ancestry refused; original host owner must reconcile')
        after = (proc / 'stat').read_text()
        later = after[after.rfind(')') + 2:].split()
        if (fields[1], fields[19]) != (later[1], later[19]):
            raise HostRefusal('host ancestry moved during observation')
        pid = int(fields[1])
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise HostRefusal('interactive human host terminal required; a TTY alone supplies no authority')
    if not (3, 12) <= sys.version_info[:2] < (3, 15):
        raise HostRefusal('selected Python must satisfy >=3.12,<3.15')


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf8')


def exclusive(path: Path, raw: bytes) -> None:
    """Original operation/receipt bytes: no replacement, durable before send."""
    literal(path, missing=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        if stream.write(raw) != len(raw):
            raise Hold(f'short write: {path}; preserve original')
        stream.flush()
        os.fsync(stream.fileno())
    if read_file(path, max(_LIMIT, len(raw))) != raw:
        raise Hold(f'exclusive readback mismatch: {path}; preserve original')
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _metadata(info):
    """Complete regular-object pin, including shared-inode change indicators."""
    return [info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_gid,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def _parents(path):
    return {str(p): _metadata(p.lstat())[:5] for p in reversed(path.parents)}


@contextmanager
def _pinned_reader(path, pin, parents, *, private=False):
    """Hold every literal parent while reading; never follow a path alias.

    Bookends detect cooperative movement. They cannot fence a hostile same-user
    writer, racing rename-away-and-back, or an uncooperative external link user.
    """
    descriptors = []
    stream = None
    try:
        if (not stat.S_ISREG(pin[2]) or _metadata(path.lstat()) != pin
                or private and (pin[3] != os.getuid() or pin[5] != 1 or stat.S_IMODE(pin[2]) != 0o600)):
            raise Hold('regular read admission/custody unavailable before open', details={'path': str(path)})
        for parent in reversed(path.parents):
            fd = os.open(str(parent) if not descriptors else parent.name,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         **({'dir_fd': descriptors[-1]} if descriptors else {}))
            descriptors.append(fd)
            if (_metadata(os.fstat(fd))[:5] != parents[str(parent)]
                    or _metadata(parent.lstat())[:5] != parents[str(parent)]):
                raise Hold('literal read parent moved', details={'path': str(parent)})
        if _metadata(os.stat(path.name, dir_fd=descriptors[-1], follow_symlinks=False)) != pin:
            raise Hold('read object moved before descriptor open', details={'path': str(path)})
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=descriptors[-1])
        stream = os.fdopen(fd, 'rb')
        opened = _metadata(os.fstat(stream.fileno()))
        if (opened != pin or _metadata(path.lstat()) != pin or not stat.S_ISREG(pin[2])
                or private and (pin[3] != os.getuid() or pin[5] != 1 or stat.S_IMODE(pin[2]) != 0o600)):
            raise Hold('read object custody or identity moved', details={'path': str(path)})
        yield stream
        if _metadata(os.fstat(stream.fileno())) != pin or _metadata(path.lstat()) != pin:
            raise Hold('read object moved during content access', details={'path': str(path)})
        for parent, fd in zip(reversed(path.parents), descriptors):
            if (_metadata(os.fstat(fd))[:5] != parents[str(parent)]
                    or _metadata(parent.lstat())[:5] != parents[str(parent)]):
                raise Hold('read parent moved during content access', details={'path': str(parent)})
    finally:
        if stream is not None:
            stream.close()
        for fd in reversed(descriptors):
            os.close(fd)


def _public_suspicion(path):
    # Preserve the original suspicion classifier. A match is neither a
    # credential finding nor permission to open a public-looking path.
    return (any(word in path.name.lower() for word in ('token', 'credential', 'secret', '.env', 'feishu', 'password'))
            or path.name in ('.npmrc', '.netrc', '.pypirc', 'auth.json') or path.suffix in ('.key', '.pem', '.p12'))


@contextmanager
def _literal_output(path):
    """New independent destination, held no-follow parents, durable publication."""
    parents = _parents(path)
    descriptors = []
    output = None
    try:
        for parent in reversed(path.parents):
            fd = os.open(str(parent) if not descriptors else parent.name,
                         os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         **({'dir_fd': descriptors[-1]} if descriptors else {}))
            descriptors.append(fd)
            if _metadata(os.fstat(fd))[:5] != parents[str(parent)]:
                raise Hold('copy destination parent moved')
        fd = os.open(path.name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=descriptors[-1])
        output = os.fdopen(fd, 'wb')
        yield output
        output.flush()
        os.fsync(output.fileno())
        pin = _metadata(os.fstat(output.fileno()))
        if (pin != _metadata(path.lstat()) or pin[3] != os.getuid() or pin[5] != 1
                or stat.S_IMODE(pin[2]) != 0o600):
            raise Hold('copy destination independent custody unavailable')
        for parent, fd in zip(reversed(path.parents), descriptors):
            if (_metadata(os.fstat(fd))[:5] != parents[str(parent)]
                    or _metadata(parent.lstat())[:5] != parents[str(parent)]):
                raise Hold('copy destination parent moved during publication')
        os.fsync(descriptors[-1])
    finally:
        if output is not None:
            output.close()
        for fd in reversed(descriptors):
            os.close(fd)


def _custody_answers(coordinator, name, rows, root, *, ordinary_state=False):
    """Finite current facts bound to machine membership, never a grant/ledger."""
    membership = digest(canonical(rows))
    print('   Recovery-role scope: ' + json.dumps({'category': name, 'root': str(root), 'objects': len(rows),
          'membership_sha256': membership, 'suspicion_paths': [r['source'] for r in rows
                                                            if _public_suspicion(Path(r['source']))]}, ensure_ascii=True))
    if ordinary_state:
        print('   This scope stays with the existing ordinary DB/config/state backup owner. Ordinary private business data may be retained there; deny exceptions identify credential/auth-secret material, unknown roles or disputed custody.')
    answer = {'category': name, 'root': str(root), 'membership_sha256': membership,
              'scope_kind': 'existing-state-backup-owner' if ordinary_state else 'public-program-or-generated',
              'disposition': input('Current custodian facts for this EXACT scope: known / mixed / unknown: '),
              'exceptions': [], 'facts': {}}
    # Preserve a declined/partial answer in the existing private failure record.
    coordinator._role_answers.append(answer)
    if answer['disposition'] not in ('known', 'mixed'):
        raise Hold('recovery role or complete writer control unknown; scope remains unopened', details={'path': str(root)})
    if answer['disposition'] == 'mixed':
        print('   Name private, unknown or disputed exceptions WITHOUT values: TYPE relative/path, one per line; blank ends. Exceptions only DENY.')
        while True:
            value = input('Exception: ')
            if not value:
                break
            kind, separator, relative = value.partition(' ')
            part = PurePosixPath(relative)
            if (kind not in ('private', 'unknown', 'disputed') or not separator or not relative
                    or part.is_absolute() or str(part) != relative or '..' in part.parts
                    or any(ord(c) < 32 for c in relative)):
                raise Hold('exception must be an exact contained path or subtree without values')
            path = root / relative
            members = [r['source'] for r in rows if Path(r['source']) == path or Path(r['source']).is_relative_to(path)]
            if not members or any(set(members) & set(e['members']) for e in answer['exceptions']):
                raise Hold('unmatched, ambiguous or overlapping deny exception', details={'path': str(path)})
            answer['exceptions'].append({'path': str(path), 'role': kind, 'members': members})
        if not answer['exceptions']:
            raise Hold('mixed scope requires actual known deny exceptions')
    material = ('Every remaining member is ordinary noncredential DB/config/state already covered by this existing backup owner. Ordinary private business data is permitted in that recovery custody; credentials, unknown or disputed material are not.'
                if ordinary_state else 'Every remaining member is public program/package material or its specified generated output; no unknown/private/disputed member is included.')
    separation = ('I know where credential/auth-secret material is separated or have named exact deny exceptions, including innocently named credentials.'
                  if ordinary_state else 'I know how private material is kept outside this scope or in the exact deny exceptions just named, including innocently named private material.')
    for key, question in (
        ('custodian', 'I am the current installation/configuration custodian and actually know the roles of every remaining member of this exact scope.'),
        ('material_role', material),
        ('private_separation', separation),
        ('writers_shared_inodes', 'I control ALL writers and shared-inode/cache users, including other hosts and external aliases, for the entire backup/preparation/migration/restart/import/return window. Unknown users mean decline.'),
    ):
        coordinator.confirm(question)
        answer['facts'][key] = True
    print('   Recorded current facts only: setup cannot prove human knowledge, historical installation or exclusion of a dishonest same-user writer.')
    return answer


class _OriginWork:
    """One cooperative budget for discovery, references, streams and compilation."""
    def __init__(self):
        self.deadline = time.monotonic() + 600
        self.entries = 0
        self.archive_bytes = 0
        self.expanded_bytes = 0

    def check(self, *, entries=0, archive_bytes=0, expanded_bytes=0):
        self.entries += entries
        self.archive_bytes += archive_bytes
        self.expanded_bytes += expanded_bytes
        if (time.monotonic() > self.deadline or self.entries > 200000
                or self.archive_bytes > 512 * _LIMIT or self.expanded_bytes > 1024 * _LIMIT):
            raise Hold('complete origin/inventory work budget exhausted; no partial acceptance')


def _private_input_directory(path):
    literal(path)
    for parent in (path, *path.parents):
        info = parent.lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, os.getuid())
                or stat.S_IMODE(info.st_mode) & 0o022):
            raise Hold('public input parents need literal independent nonwritable custody', details={'path': str(parent)})
    if path.lstat().st_uid != os.getuid() or stat.S_IMODE(path.lstat().st_mode) != 0o700:
        raise Hold('public input directory must be owned private 0700 custody', details={'path': str(path)})


def _discovery_digest(graph):
    # Original SQLite ownership includes RO SHM readmark writes. Preserve the
    # observed metadata but do not misrepresent readmark timestamps as immutable
    # source contents. Main/WAL membership and every object identity still bind.
    rows = {}
    for name, row in graph.items():
        value = dict(row)
        if name.endswith('-shm'):
            value['metadata'] = row['metadata'][:-2] + [0, 0]
            value['mtime_ns'] = value['ctime_ns'] = 0
        rows[name] = value
    return digest(canonical(rows))


def _backup_link_closure(path, work):
    """Metadata-only equivalent of strict resolution, retaining EVERY hop."""
    pending = list(path.parts[1:])
    current, links = Path('/'), []
    while pending:
        work.check()
        part = pending.pop(0)
        if part in ('', '.'):
            continue
        if part == '..':
            current = current.parent
            continue
        candidate = current / part
        info = candidate.lstat()
        if stat.S_ISLNK(info.st_mode):
            work.check(entries=1)
            if len(links) >= 64:
                raise Hold('allowed link closure cycle or hop limit', details={'path': str(path)})
            target = os.readlink(candidate)
            link = {'path': str(candidate), 'metadata': _metadata(info), 'parents': _parents(candidate), 'link': target}
            if _metadata(candidate.lstat()) != link['metadata']:
                raise Hold('allowed intermediate link moved', details={'path': str(candidate)})
            links.append(link)
            parts = PurePosixPath(target).parts
            if target.startswith('/'):
                current = Path('/')
                parts = parts[1:]
            pending = list(parts) + pending
        else:
            if pending and not stat.S_ISDIR(info.st_mode):
                raise Hold('allowed link has a non-directory parent', details={'path': str(candidate)})
            current = candidate
    literal(current)
    work.check()
    return current, links


class _PublicOrigins:
    """Private producer for the existing backup, not a new adoption interface.

    No candidates/RECORD/package manifests are opened to discover membership.
    References are admitted before reading; suspects need independent expected
    bytes before their descriptor is opened. Every accepted copy is ORIGINAL.
    """
    def __init__(self, coordinator, graph, inventory, work):
        self.owner, self.graph, self.inventory, self.work = coordinator, graph, inventory, work
        self.directory = coordinator.backup / 'origins'
        self.directory.mkdir(mode=0o700)
        self.references = inventory['references'] = []
        self.expected = {}
        self.archives = []
        self.git_entries = {}
        self.git_trees = {}
        self.commit = coordinator.source_pin['head']
        self.reference_count = 0
        self.archive_input = None

    def retain(self, raw, provenance):
        self.work.check()
        if len(raw) > 64 * _LIMIT:
            raise Hold('selected public reference exceeds 64MiB')
        self.reference_count += 1
        path = self.directory / str(self.reference_count)
        exclusive(path, raw)
        row = {'path': str(path), 'metadata': _metadata(path.lstat()), 'parents': _parents(path),
               'sha256': digest(raw), 'size': len(raw), 'origin': provenance}
        self.references.append(row)
        self.work.check()
        return row

    def git_tree(self):
        if self.commit in self.git_trees:
            self.git_entries = self.git_trees[self.commit]
            return
        if self.git_entries:
            return
        _, out, err = self.owner.command([self.owner.tools['git']['path'], 'ls-tree', '-r', '-z', '--full-tree', self.commit])
        if err:
            raise Hold('pinned Git membership unavailable')
        for entry in out.split('\0'):
            if not entry:
                continue
            self.work.check(entries=1)
            header, name = entry.split('\t', 1)
            mode, kind, oid = header.split(' ')
            if (str(PurePosixPath(name)) != name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
                    or any(ord(c) < 32 for c in name) or name in self.git_entries
                    or re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', oid) is None):
                raise Hold('unsupported or ambiguous pinned Git path')
            self.git_entries[name] = (mode, kind, oid)
        self.git_trees[self.commit] = self.git_entries

    def git_bytes(self, name):
        self.git_tree()
        entry = self.git_entries.get(name)
        if entry is None or entry[0] not in ('100644', '100755') or entry[1] != 'blob':
            raise Hold('no exact regular pinned Git reference', details={'path': str(self.owner.source / name)})
        raw = self.owner._git_blob(entry[2], self.work)
        reference = self.retain(raw, {'kind': 'git-blob', 'commit': self.commit, 'path': name, 'object': entry[2], 'mode': entry[0]})
        return raw, reference

    def admit_graph(self):
        groups = {}
        source = self.owner.source
        for row in self.graph.values():
            if row.get('excluded') or not stat.S_ISREG(row['metadata'][2]) and 'link' not in row:
                continue
            path = Path(row['source'])
            if path == self.owner.home / 'daemon.token' and stat.S_ISREG(row['metadata'][2]):
                # Existing credential owner: ensure_token returns an existing
                # token without writing; setup's uv/npm/build/migration outputs
                # cannot target this file. Never hash/copy it or generalize by
                # a credential-looking basename.
                self.owner.confirm('Existing daemon.token stays in its original usable custody, unopened/uncopied by backup. I control all its writers/shared aliases throughout maintenance; no operation may remove/replace it. The existing startup owner reads an existing token, never regenerates it.')
                row.update(role='private-untouched', disposition='original credential custody; identity only; contents never read or copied',
                           untouched_reason='exact daemon.token: existing ensure_token returns without write; selected preparation/migration outputs exclude this original file')
                continue
            if path.is_relative_to(source / '.venv'):
                category, root = 'installed-python-and-generated-caches', source / '.venv'
            elif path.is_relative_to(source / 'web/node_modules'):
                category, root = 'installed-node-and-upstream-generated', source / 'web/node_modules'
            elif path.is_relative_to(source / 'web/dist') or path.is_relative_to(source / 'web/.feature-flags'):
                category, root = 'all-old-generated-ui', source / 'web'
            elif path.is_relative_to(source):
                category, root = 'checkout-and-old-build-outputs', source
            else:
                # Original DB/config copy owner remains in charge. Current
                # exceptions apply even to an innocently named private file.
                category, root = 'shared-daemon-runtime-org-recovery', Path(row['root'])
            groups.setdefault((category, root), []).append(row)
        for (category, root), rows in groups.items():
            ordinary = category == 'shared-daemon-runtime-org-recovery'
            answer = _custody_answers(self.owner, category, rows, root, ordinary_state=ordinary)
            for row in rows:
                row['role'] = ('ordinary-recovery-state' if ordinary else 'public-generated'
                               if category in ('installed-python-and-generated-caches', 'all-old-generated-ui') else 'public-program')
                row['role_scope'] = answer['membership_sha256']
                for exception in answer['exceptions']:
                    if row['source'] in exception['members']:
                        row['role'] = exception['role']
        # Check the entire alias graph before opening ANY candidate. nlink is
        # not a census of out-of-scope aliases; shared-user control is required.
        aliases = {}
        for row in self.graph.values():
            if stat.S_ISREG(row['metadata'][2]) and not row.get('excluded'):
                aliases.setdefault(tuple(row['metadata'][:2]), []).append(row)
        self.inventory['aliases'] = []
        for row in self.graph.values():
            if 'link' in row and row.get('role') not in ('public-program', 'public-generated'):
                raise Hold('private/unknown/disputed allowed-link role; no referent access', details={'path': row['source']})
            if row.get('role') == 'ordinary-recovery-state' and _public_suspicion(Path(row['source'])):
                raise Hold('suspect state slot has no independent public origin; original custody retained unopened', details={'path': row['source']})
        for key, rows in aliases.items():
            roles = {r.get('role', 'unknown') for r in rows}
            nlink = rows[0]['metadata'][5]
            if nlink > 1:
                self.inventory['aliases'].append({'inode': key, 'members': [r['source'] for r in rows],
                                                  'nlink': nlink, 'unobserved_alias_count': nlink - len(rows),
                                                  'limit': 'selected paths/content only; external hardlink topology is not restored'})
            untouched = len(rows) == 1 and roles == {'private-untouched'}
            allowed = {'public-program', 'public-generated', 'ordinary-recovery-state'}
            conflict = 'ordinary-recovery-state' in roles and len(roles) > 1
            if nlink < len(rows) or not untouched and (roles - allowed or conflict):
                # Role differences include innocently named private aliases.
                bad = next(r for r in rows if r.get('role') not in ('public-program', 'public-generated')) if roles - {'public-program', 'public-generated'} else rows[0]
                raise Hold('private/unknown/disputed recovery role or alias conflict; no candidate access', details={
                    'path': bad['source'], 'custodian': 'current source/environment or shared-store custodian',
                    'proposed_operation': 'original recovery copy',
                    'C07_remover': 'uv/npm/build for source scopes; original migration/start for stores',
                    'required_action': 'return exact indispensable protected object to Engineering before access'})
        self.close_links()

    def close_links(self):
        for row in self.graph.values():
            if 'link' not in row:
                continue
            external_links = []
            for link in row['link_closure']:
                member = self.graph.get(link['path'])
                if member is not None and not member.get('excluded'):
                    if (member.get('role') not in ('public-program', 'public-generated')
                            or member['metadata'] != link['metadata'] or member.get('link') != link['link']):
                        raise Hold('intermediate allowed-link role/identity conflict', details={'path': link['path']})
                elif any(Path(link['path']).is_relative_to(Path(root)) for root in (
                        str(self.owner.home), str(self.owner.source), *self.owner.frozen['registry']['registered'])):
                    raise Hold('uncovered intermediate affected link', details={'path': link['path']})
                else:
                    external_links.append(link)
            target = self.graph.get(row['referent'])
            if target is not None and not target.get('excluded'):
                if external_links or target.get('role', 'public-program') not in ('public-program', 'public-generated'):
                    raise Hold('allowed link has private/unknown referent', details={'path': row['source']})
                row['referent_coverage'] = {'source': target['source'], 'kind': 'inventoried-original'}
            else:
                # The only preexisting external link family with a complete
                # untouched effect proof here is the selected base interpreter.
                # uv --python uses it, never writes it; npm/build/migrations
                # write selected source/stores, not that external executable.
                pin = self.owner.python_pin
                if (row['source'] not in tuple(str(self.owner.source / '.venv/bin' / n) for n in ('python', 'python3', f'python{sys.version_info.major}.{sys.version_info.minor}'))
                        or row['referent'] != pin['target'] or identity(Path(row['referent'])) != pin['identity']
                        or any(Path(row['referent']).is_relative_to(Path(root)) for root in (str(self.owner.source), str(self.owner.home), *self.owner.frozen['registry']['registered']))):
                    raise Hold('indispensable allowed referent has no covered or proven-untouched custody', details={'path': row['source']})
                self.owner.confirm('The selected external base interpreter ' + json.dumps(pin['target']) + ' and its complete original link chain ' + json.dumps([p['path'] for p in external_links]) + ' remain usable in original custody throughout recovery. No uv/npm/build/migration/start or other host actor will replace them; I control all writers. This is the only external referent exception.')
                row['referent_coverage'] = {'kind': 'untouched-selected-base-interpreter', 'identity': pin,
                                          'external_links': external_links,
                                          'effect_reason': 'all preparation/migration outputs remain in selected source/stores; base interpreter is input only'}

    def prepare_inputs(self):
        suspects = [r for r in self.graph.values() if r.get('role') in ('public-program', 'public-generated')
                    and stat.S_ISREG(r['metadata'][2]) and _public_suspicion(Path(r['source']))]
        installed = [r for r in suspects if Path(r['source']).is_relative_to(self.owner.source / '.venv')
                     or Path(r['source']).is_relative_to(self.owner.source / 'web/node_modules')]
        if not installed:
            return
        previous = input('Matched installed public slots need exact locks. If current locks do not describe the OLD installation, select its full previously reviewed source commit; blank uses current: ')
        if previous:
            if re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', previous) is None:
                raise Hold('prior reviewed lock selection must be a full commit')
            self.owner.confirm('I know the selected prior commit ' + previous + ' is reviewed public source/locks under current controlled Git reference custody; this is not a historical installation certificate.')
            _, actual, err = self.owner.command([self.owner.tools['git']['path'], 'rev-parse', '--verify', previous + '^{commit}'])
            if err or actual.strip() != previous:
                raise Hold('prior exact reviewed commit unavailable')
            self.commit = previous
        uv_raw, _ = self.git_bytes('uv.lock')
        npm_raw, _ = self.git_bytes('web/package-lock.json')
        if len(uv_raw) > _LIMIT or len(npm_raw) > _LIMIT:
            raise Hold('public lock input exceeds 1MiB')
        uv = tomllib.loads(uv_raw.decode('utf8'))
        npm = json.loads(npm_raw, object_pairs_hook=self.unique_pairs)
        self.lock_artifacts = []
        for package in uv.get('package', []):
            for wheel in package.get('wheels', []):
                self.work.check(entries=1)
                if (not isinstance(wheel, dict) or re.fullmatch(r'sha256:[0-9a-f]{64}', wheel.get('hash', '')) is None
                        or type(wheel.get('size')) is not int or wheel['size'] <= 0):
                    raise Hold('unsupported exact wheel lock entry')
                self.public_url(wheel.get('url'))
                if not self.wheel_supported(wheel['url']):
                    continue
                self.lock_artifacts.append({'kind': 'wheel', 'package': package['name'], 'version': package['version'],
                                            'source': package.get('source'), 'sha256': wheel['hash'][7:],
                                            'size': wheel['size'], 'url': wheel['url']})
        self.node_packages = npm.get('packages')
        if type(self.node_packages) is not dict:
            raise Hold('exact Node lock installation paths unavailable')
        needed_node = set()
        for row in installed:
            path = Path(row['source'])
            if path.is_relative_to(self.owner.source / 'web/node_modules'):
                key, member = self.node_selector(path)
                row['selector'] = {'lock_install_path': key, 'member': member}
                needed_node.add(key)
        for key in sorted(needed_node):
            package = self.node_packages[key]
            integrity = package.get('integrity', '')
            match = re.fullmatch(r'(sha256|sha384|sha512)-([A-Za-z0-9+/]+={0,2})', integrity)
            if not match:
                raise Hold('unsupported exact Node lock integrity', details={'path': key})
            try:
                hash_bytes = base64.b64decode(match[2], validate=True)
            except ValueError:
                raise Hold('invalid Node lock integrity') from None
            if len(hash_bytes) != hashlib.new(match[1]).digest_size:
                raise Hold('invalid Node lock integrity length')
            artifact = {'kind': 'node', 'lock_install_path': key, 'algorithm': match[1],
                        'hash': hash_bytes.hex(), 'integrity': integrity, 'url': package.get('resolved')}
            if isinstance(artifact['url'], str) and artifact['url'].startswith('file:'):
                name = str(PurePosixPath('web') / artifact['url'][5:])
                if '..' in PurePosixPath(name).parts:
                    raise Hold('unsupported vendored archive path')
                raw, reference = self.git_bytes(name)
                if hashlib.new(artifact['algorithm'], raw).hexdigest() != artifact['hash']:
                    raise Hold('vendored Git archive does not equal exact Node lock')
                self.archives.append({'reference': reference, 'artifacts': [artifact], 'members': None})
            else:
                self.public_url(artifact['url'])
                self.lock_artifacts.append(artifact)
        self.inventory['public_archive_candidates'] = self.lock_artifacts
        needed = self.owner.backup / 'public-inputs-needed.json'
        exclusive(needed, canonical({'source_commit': self.commit, 'installed_suspicion_paths': [r['source'] for r in installed],
                                    'exact_lock_artifacts': self.lock_artifacts,
                                    'instruction': 'Human later supplies compatible anonymous exact-lock public archives in independent literal private custody; setup never downloads or reads auth config. No per-file ledger.'}))
        self.inventory['public_input_instruction'] = self.owner._retained_pin(needed, self.work)
        print('   Exact reviewed lock URLs/hashes and required installed slots: ' + json.dumps(str(needed)) + '. Missing inputs hold all preparation; human anonymous acquisition is separate.')
        needs_external = any(Path(r['source']).is_relative_to(self.owner.source / '.venv') for r in installed) or any(
            a['kind'] == 'node' for a in self.lock_artifacts)
        if not needs_external:
            return
        default = self.owner.backup.parent / 'public-origin-inputs' / self.owner.source_pin['tree']
        chosen = input('Literal private directory of retained anonymous public exact-lock archives (no inputs/roles/mapping JSON). Blank selects ' + json.dumps(str(default)) + ': ')
        try:
            directory = absolute_path(chosen) if chosen else default
        except argparse.ArgumentTypeError:
            raise Hold('public archive directory must be a literal absolute path') from None
        if any(directory.is_relative_to(Path(root)) or Path(root).is_relative_to(directory)
               for root in (str(self.owner.source), str(self.owner.backup), *self.owner.frozen['registry']['registered'])):
            raise Hold('reference inputs overlap affected source/runtime/recovery')
        self.owner.diagnostic.paths['selected_public_archive_directory'] = str(directory)
        _private_input_directory(directory)
        names = tuple(sorted(p.name for p in directory.iterdir()))
        pins = []
        for name in names:
            self.work.check(entries=1)
            path = directory / name
            info = _metadata(path.lstat())
            if (not stat.S_ISREG(info[2]) or info[3] != os.getuid() or info[5] != 1
                    or stat.S_IMODE(info[2]) != 0o600):
                raise Hold('reference archive must be an independent private regular 0600 file', details={'path': str(path)})
            pins.append({'source': str(path), 'metadata': info, 'parents': _parents(path)})
        if not pins:
            raise Hold('missing exact-lock public archives; human may obtain the pinned anonymous public URLs later; setup never downloads')
        _custody_answers(self.owner, 'independent-public-archive-references', pins, directory)
        answer = self.owner._role_answers[-1]
        if answer['exceptions']:
            raise Hold('private/unknown reference input remains unopened; archive selection cannot include it')
        self.archive_input = {'path': str(directory), 'metadata': _metadata(directory.lstat()),
                              'parents': _parents(directory), 'members': names, 'files': pins}
        self.inventory['archive_input'] = self.archive_input
        for pin in pins:
            path = Path(pin['source'])
            self.work.check(archive_bytes=pin['metadata'][6])
            with _pinned_reader(path, pin['metadata'], pin['parents'], private=True) as stream:
                raw = self.read_bounded(stream, 512 * _LIMIT)
            hashes = {a: hashlib.new(a, raw).hexdigest() for a in ('sha256', 'sha384', 'sha512')}
            matches = [a for a in self.lock_artifacts if
                       (a['kind'] == 'wheel' and a['sha256'] == hashes['sha256'] and a['size'] == len(raw)
                        or a['kind'] == 'node' and a['hash'] == hashes[a['algorithm']])]
            if not matches or len({a['kind'] for a in matches}) != 1:
                raise Hold('unjoined or ambiguous whole public archive; filename/package spelling is insufficient', details={'path': str(path)})
            pin['sha256'] = hashes['sha256']
            # Whole archives may exceed selected-member 64MiB. Retain them as
            # original admitted inputs, with independent byte-for-byte copies.
            reference_path = self.directory / ('archive-' + str(len(self.archives)))
            self.owner.copy_regular(path, reference_path, pin=pin['metadata'], parents=pin['parents'],
                                    expected=raw, work=self.work)
            reference = {'path': str(reference_path), 'metadata': _metadata(reference_path.lstat()),
                         'parents': _parents(reference_path), 'sha256': hashes['sha256'], 'size': len(raw),
                         'origin': {'kind': 'whole-lock-archive', 'matches': matches}}
            self.references.append(reference)
            self.archives.append({'reference': reference, 'artifacts': matches, 'members': None})
        self.work.check()

    @staticmethod
    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise Hold('duplicate public lock key')
            result[key] = value
        return result

    @staticmethod
    def public_url(value):
        if not isinstance(value, str):
            raise Hold('missing public archive URL')
        parsed = urlsplit(value)
        if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
            raise Hold('archive locator is not supported anonymous public HTTPS; no credential lookup')

    @staticmethod
    def wheel_supported(url):
        # Finite wheel tags without importing installed packaging/candidates.
        # Unsupported interpreters, ABI or libc/platform tags remain unavailable.
        name = urlsplit(url).path.rsplit('/', 1)[-1]
        parts = name[:-4].split('-') if name.endswith('.whl') else []
        if len(parts) not in (5, 6):
            return False
        python, abi, platform = parts[-3:]
        if abi == 'none' and platform == 'any' and 'py3' in python.split('.'):
            return True
        current = f'cp{sys.version_info.major}{sys.version_info.minor}'
        exact = python == current and abi == current
        older = re.fullmatch(r'cp3([0-9]+)', python)
        stable = abi == 'abi3' and older is not None and 2 <= int(older[1]) <= sys.version_info.minor
        if not (exact or stable):
            return False
        machine = os.uname().machine
        libc = re.fullmatch(r'glibc ([0-9]+)\.([0-9]+)', os.confstr('CS_GNU_LIBC_VERSION') or '')
        if libc is None:
            return False
        running = (int(libc[1]), int(libc[2]))
        for tag in platform.split('.'):
            match = re.fullmatch(r'manylinux_([0-9]+)_([0-9]+)_' + re.escape(machine), tag)
            if match and (int(match[1]), int(match[2])) <= running:
                return True
            for legacy, minimum in (('manylinux1', (2, 5)), ('manylinux2010', (2, 12)), ('manylinux2014', (2, 17))):
                if tag == legacy + '_' + machine and minimum <= running:
                    return True
        return False

    def read_bounded(self, stream, maximum):
        blocks, size = [], 0
        while True:
            self.work.check()
            block = stream.read(min(_LIMIT, maximum + 1 - size))
            if not block:
                break
            blocks.append(block)
            size += len(block)
            if size > maximum:
                raise Hold('public member/source/recipe read size limit')
        return b''.join(blocks)

    def node_selector(self, path):
        relative = path.relative_to(self.owner.source / 'web').as_posix()
        keys = [key for key in self.node_packages if key and relative.startswith(key + '/')]
        if not keys:
            raise Hold('no exact Node lock installation path', details={'path': str(path)})
        key = max(keys, key=len)
        if (not key.startswith('node_modules/') or '..' in PurePosixPath(key).parts
                or str(PurePosixPath(key)) != key or self.node_packages[key].get('link')):
            raise Hold('unsupported Node lock installation path')
        return key, 'package/' + relative[len(key) + 1:]

    @staticmethod
    def archive_name(name):
        value = name[:-1] if name.endswith('/') else name
        path = PurePosixPath(value)
        if (not value or path.is_absolute() or str(path) != value or '..' in path.parts or '\\' in name
                or any(ord(c) < 32 for c in name)):
            raise Hold('unsafe public archive namespace')
        return value

    def archive_members(self, archive):
        if archive['members'] is not None:
            return archive['members']
        ref = archive['reference']
        with _pinned_reader(Path(ref['path']), ref['metadata'], ref['parents'], private=True) as stream:
            raw = self.read_bounded(stream, 512 * _LIMIT)
        if digest(raw) != ref['sha256']:
            raise Hold('retained lock archive changed')
        wheel = archive['artifacts'][0]['kind'] == 'wheel'
        members = {}
        container = zipfile.ZipFile(io.BytesIO(raw)) if wheel else tarfile.open(fileobj=io.BytesIO(raw), mode='r:gz')
        try:
            iterator = container.infolist() if wheel else container
            for info in iterator:
                self.work.check(entries=1)
                name = self.archive_name(info.filename if wheel else info.name)
                if name in members:
                    raise Hold('duplicate/ambiguous public archive member')
                if wheel:
                    mode = info.external_attr >> 16
                    kind = 'directory' if info.is_dir() else 'file' if stat.S_IFMT(mode) in (0, stat.S_IFREG) else 'alias-or-special'
                    size = info.file_size
                    encrypted = bool(info.flag_bits & 1)
                else:
                    kind = 'directory' if info.isdir() else 'file' if info.type in (tarfile.REGTYPE, tarfile.AREGTYPE) and not info.sparse else 'alias-or-special'
                    size = info.size
                    encrypted = False
                    if info.issym() or info.islnk():
                        target = self.archive_name(info.linkname)
                        if target.startswith('/'):
                            raise Hold('unsafe public archive alias')
                if type(size) is not int or size < 0:
                    raise Hold('invalid public archive member size')
                self.work.check(expanded_bytes=size)
                members[name] = {'kind': kind, 'info': info, 'size': size, 'encrypted': encrypted}
            for name in members:
                self.work.check()
                for parent in PurePosixPath(name).parents:
                    if str(parent) in members and members[str(parent)]['kind'] != 'directory':
                        raise Hold('public archive contains aliased/file parent namespace')
            # The container is retained only in memory, without extraction or
            # execution. The original retained archive is separately sealed.
            archive['container'] = container
            archive['members'] = members
            return members
        except BaseException:
            container.close()
            raise

    def package_bytes(self, path):
        node = path.is_relative_to(self.owner.source / 'web/node_modules')
        if node:
            key, selector = self.node_selector(path)
        else:
            relative = path.relative_to(self.owner.source / '.venv').as_posix()
            prefix = f'lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages/'
            if not relative.startswith(prefix):
                raise Hold('unsupported installed Python origin slot', details={'path': str(path)})
            selector = relative[len(prefix):]
        choices = []
        for archive in self.archives:
            artifacts = archive['artifacts']
            if node and not any(a['kind'] == 'node' and a['lock_install_path'] == key for a in artifacts):
                continue
            if not node and artifacts[0]['kind'] != 'wheel':
                continue
            members = self.archive_members(archive)
            names = [selector] if node else [name for name in members if name == selector or
                    re.fullmatch(r'[^/]+\.data/(purelib|platlib)/' + re.escape(selector), name)]
            for name in names:
                item = members.get(name)
                if item is None:
                    continue
                if item['kind'] != 'file' or item['encrypted'] or item['size'] > 64 * _LIMIT:
                    raise Hold('selected package member is not an ordinary bounded public file')
                self.work.check(expanded_bytes=item['size'])
                container = archive['container']
                stream = container.open(item['info']) if not node else container.extractfile(item['info'])
                if stream is None:
                    raise Hold('ordinary public archive member unavailable')
                with stream:
                    raw = self.read_bounded(stream, 64 * _LIMIT)
                if len(raw) != item['size']:
                    raise Hold('public member size mismatch')
                choices.append((raw, archive, name))
        if len(choices) != 1:
            raise Hold('missing or ambiguous exact locked public member; supply the exact OLD lock/archive, never rebuild recovery', details={'path': str(path)})
        raw, archive, member = choices[0]
        ref = self.retain(raw, {'kind': 'locked-member', 'commit': self.commit,
                               'archive': archive['reference']['path'], 'member': member,
                               'lock': 'web/package-lock.json' if node else 'uv.lock',
                               'artifacts': archive['artifacts']})
        return raw, ref

    def expectation(self, row):
        path = Path(row['source'])
        if row['source'] in self.expected:
            return self.expected[row['source']]
        if row.get('role') not in ('public-program', 'public-generated'):
            raise Hold('unadmitted candidate remains unopened', details={'path': str(path)})
        if path.suffix == '.pyc' and path.parent.name == '__pycache__':
            return self.pyc_expectations(row)
        if path.is_relative_to(self.owner.source / '.venv') or path.is_relative_to(self.owner.source / 'web/node_modules'):
            raw, ref = self.package_bytes(path)
        else:
            if not path.is_relative_to(self.owner.source):
                raise Hold('suspicious non-source object lacks an admitted exact public origin', details={'path': str(path)})
            # Candidate source is always compared with the CURRENT reviewed
            # checkout, even if OLD installed dependencies selected prior locks.
            selected, entries = self.commit, self.git_entries
            self.commit, self.git_entries = self.owner.source_pin['head'], {}
            try:
                raw, ref = self.git_bytes(path.relative_to(self.owner.source).as_posix())
            finally:
                self.commit, self.git_entries = selected, entries
        result = ([raw], ref)
        self.expected[row['source']] = result
        return result

    def build_expectations(self):
        # Finish all independent selectors, public bytes and generated recipe
        # edges before the first candidate content read. Unknown/missing origin
        # is not discovered by trying an installed header or local manifest.
        for row in self.graph.values():
            self.work.check()
            if (row.get('role') in ('public-program', 'public-generated')
                    and stat.S_ISREG(row['metadata'][2]) and _public_suspicion(Path(row['source']))):
                variants, origin = self.expectation(row)
                row['origin'] = origin
                row['independent_expected_sha256'] = [digest(raw) for raw in variants]
        for name, (variants, origin) in self.expected.items():
            self.graph[name]['origin'] = origin
            self.graph[name]['independent_expected_sha256'] = [digest(raw) for raw in variants]
        self.work.check()

    def pyc_expectations(self, row):
        path = Path(row['source'])
        tag = re.escape(sys.implementation.cache_tag or '')
        match = re.fullmatch(r'(.+)\.' + tag + r'(?:\.opt-([12]))?\.pyc', path.name)
        if (not match or sys.implementation.name != 'cpython' or sys.flags.debug
                or sysconfig.get_config_var('Py_DEBUG') or sysconfig.get_config_var('Py_GIL_DISABLED')
                or set(sys._xoptions) - {'no_debug_ranges'}
                or marshal.version != (5 if sys.version_info[:2] == (3, 14) else 4)
                or self.owner.python_pin['target'] != str(Path(sys.executable).resolve(strict=True))):
            raise Hold('unsupported finite CPython cache/compiler configuration', details={'path': str(path)})
        source = path.parent.parent / (match[1] + '.py')
        paired = self.graph.get(str(source))
        if paired is None or paired.get('role') not in ('public-program', 'public-generated'):
            raise Hold('generated cache has no admitted paired public source', details={'path': str(path)})
        # Derive exclusively from independent source bytes. copy_row enforces
        # actual paired-source equality/copy before opening the generated file,
        # including when __pycache__ precedes the source in lexical traversal.
        if source.is_relative_to(self.owner.source / '.venv'):
            raw, reference = self.package_bytes(source)
        elif source.is_relative_to(self.owner.source) and not source.is_relative_to(self.owner.source / 'web/node_modules'):
            selected, entries = self.commit, self.git_entries
            self.commit, self.git_entries = self.owner.source_pin['head'], {}
            try:
                raw, reference = self.git_bytes(source.relative_to(self.owner.source).as_posix())
            finally:
                self.commit, self.git_entries = selected, entries
        else:
            raise Hold('unsupported generated-source origin')
        if len(raw) > 8 * _LIMIT:
            raise Hold('compiler source exceeds 8MiB')
        self.expected[str(source)] = ([raw], reference)
        row['dependencies'] = [str(source)]
        self.work.check()
        optimize = int(match[2] or '0')
        code = compile(raw, str(source), 'exec', flags=0, dont_inherit=True, optimize=optimize)
        payload = marshal.dumps(code, marshal.version)
        self.work.check()
        stamp = struct.pack('<II', (paired['metadata'][7] // 1000000000) & 0xffffffff, len(raw) & 0xffffffff)
        source_hash = importlib.util.source_hash(raw)
        alternatives = [importlib.util.MAGIC_NUMBER + struct.pack('<I', flags) + header + payload
                        for flags, header in ((0, stamp), (1, source_hash), (3, source_hash))]
        recipe = {'kind': 'finite-cpython-cache', 'source': str(source), 'source_reference': reference['path'],
                  'source_metadata': paired['metadata'], 'cache_tag': sys.implementation.cache_tag,
                  'compiler': self.owner.python_pin, 'marshal_version': marshal.version,
                  'magic': importlib.util.MAGIC_NUMBER.hex(), 'flags': [0, 1, 3], 'optimize': optimize,
                  'debug_ranges_disabled': bool(sys._xoptions.get('no_debug_ranges') or os.environ.get('PYTHONNODEBUGRANGES')),
                  'filename': str(source), 'compile_flags': 0, 'dont_inherit': True}
        refs = [self.retain(data, recipe | {'expected_flags': flags}) for flags, data in zip((0, 1, 3), alternatives)]
        result = (alternatives, {'recipe': recipe, 'alternatives': [ref['path'] for ref in refs]})
        self.expected[row['source']] = result
        return result

    def copy_row(self, row, *, force_expected=False):
        if 'backup' in row:
            # A dependency copied early still needs exact original bookends.
            if _metadata(Path(row['source']).lstat()) != row['metadata']:
                raise Hold('paired original source moved after copy', details={'path': row['source']})
            if force_expected:
                variants, origin = self.expectation(row)
                with _pinned_reader(Path(row['source']), row['metadata'], row['parents']) as stream:
                    raw = self.read_bounded(stream, 8 * _LIMIT)
                if raw not in variants or digest(raw) != row['sha256']:
                    raise Hold('paired original source/copy disagrees with independent source')
                with _pinned_reader(Path(row['backup']), row['backup_metadata'], row['backup_parents'], private=True) as stream:
                    if self.read_bounded(stream, 8 * _LIMIT) != raw:
                        raise Hold('paired original copy differs from independent bytes')
                row.update(origin=origin, expected_sha256=digest(raw))
            return
        self.work.check()
        path = Path(row['source'])
        expected, origin = (None, None)
        if force_expected or _public_suspicion(path) or row['source'] in self.expected:
            expected, origin = self.expectation(row)
        for dependency in row.get('dependencies', []):
            self.copy_row(self.graph[dependency], force_expected=True)
        target = self.owner.backup / 'files' / row['slot']
        self.owner.copy_regular(path, target, pin=row['metadata'], parents=row['parents'],
                                expected=expected, work=self.work)
        row.update(kind='file', backup=str(target), restore=str(path),
                   sha256=self.owner.hash_file(target, private=True, work=self.work),
                   backup_metadata=_metadata(target.lstat()), backup_parents=_parents(target))
        if origin is not None:
            row['origin'] = origin
            row['expected_sha256'] = row['sha256']
        self.work.check()

    def close(self):
        for archive in self.archives:
            if archive.get('container') is not None:
                archive['container'].close()


class Coordinator:
    def __init__(self, args: argparse.Namespace, diagnostic: DiagnosticContext) -> None:
        self.diagnostic = diagnostic
        diagnostic.enter('constructor-path-custody')
        self.args = args
        self.home = literal(Path(os.environ.get('HAPPYRANCH_DAEMON_HOME') or str(Path.home() / '.happyranch')))
        self.source = literal(_SOURCE)
        self.home_pin = identity(self.home)[:4]
        if identity(self.home)[3] != os.getuid() or identity(self.source)[3] != os.getuid():
            raise Hold('source/daemon-home custody differs from the human host user')
        self.receipts = None
        self.counter = 0
        self.last_layout = 'not yet accepted'
        self.commands = []
        self.boots = None
        self.accepted_backup = None
        self.original_process = None
        self.python = self.source / '.venv' / 'bin' / 'python'
        # The human established control before environment/helper/constructor
        # effects. This selection is independently observed, never a fallback
        # after a failed stop in this invocation.
        diagnostic.enter('constructor-entry-boundary')
        registered = registry(self.home)
        self.runtime = literal(args.runtime_root or Path(registered['active']))
        self.frozen = {'registry': registered}
        for name in ('daemon.pid', 'daemon.port'):
            literal(self.home / name, missing=True)
        self.entry_running = (self.home / 'daemon.pid').exists()
        self.offline = not self.entry_running
        if self.entry_running:
            self.entry_lifecycle = self.running_lifecycle()
        else:
            self.offline_lifecycle = self.stopped_lifecycle()
        self.process_boundary(offline=self.offline)
        diagnostic.observations['entry'] = 'running' if self.entry_running else 'positive-stopped; stop not attempted'
        diagnostic.enter('constructor-python')
        self.python_pin = self.python_identity()
        self.cli = [str(self.python), '-m', 'cli.main']
        self.tools = {}
        for name in ('git', 'uv', 'node', 'npm', 'bash'):
            diagnostic.enter('constructor-tool-' + name)
            self.tools[name] = self.tool(name)
        diagnostic.enter('constructor-environment')
        self.environment = dict(os.environ)
        for key in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT', 'UV_PYTHON', 'UV_WORKING_DIRECTORY', 'UV_PROJECT'):
            if key in os.environ:
                raise Hold(f'unsupported {key} redirection; original environment owner must reconcile without stripping it')
        if os.environ.get('VIRTUAL_ENV') not in (None, str(self.source / '.venv')):
            raise Hold('VIRTUAL_ENV differs from the selected source environment')
        diagnostic.enter('constructor-source')
        self.source_pin = self.prepare_source()
        for name in ('uv', 'node', 'npm'):
            diagnostic.enter('constructor-version-' + name)
            code, version, err = self.command([self.tools[name]['path'], '--version'])
            if err or (name == 'node' and re.fullmatch(r'v24\.[0-9]+\.[0-9]+\n', version) is None):
                raise Hold(f'{name} version unavailable/unsupported; Node24 required before maintenance')
            self.tools[name]['version'] = version.strip()
        diagnostic.enter('constructor-registry')
        if registry(self.home) != registered:
            raise Hold('runtime registry moved during constructor')
        diagnostic.enter('constructor-maintenance')
        diagnostic.pre_effect = False  # RO SQLite may affect reader sidecars.
        self.frozen = observe_maintenance(self.home, self.runtime)
        if self.frozen['registry'] != registered:
            raise Hold('runtime registry moved during constructor')
        self.process_boundary(offline=self.offline)
        if self.entry_running:
            if self.running_lifecycle() != self.entry_lifecycle:
                raise Hold('original running lifecycle moved during constructor')
            self.live_scope()
        elif self.stopped_lifecycle() != self.offline_lifecycle:
            raise Hold('stopped lifecycle moved during constructor')
        self.accepted_layouts = {o['path']: o['layout'] for o in self.frozen['orgs']}
        self.target = next((o for o in self.frozen['orgs'] if o['runtime'] == str(self.runtime) and o['org'] == args.org), None)
        if self.target is None:
            raise Hold('target org is not an existing admitted registered org; org creation is excluded')
        self.last_layout = self.target['layout']
        diagnostic.layout = self.last_layout
        diagnostic.enter('constructor-settings-ui')
        from runtime.config import Settings
        self.settings = Settings()
        self.config_pin = digest(read_file(self.home / 'config.yaml')) if (self.home / 'config.yaml').exists() else None
        if (literal(self.settings.project_root) != self.source
                or os.environ.get('HAPPYRANCH_WEB_DIST') not in (None, str(self.source / 'web/dist'))):
            raise Hold('served UI resolves outside the selected source; original source/UI owner must reconcile')
        self.dist = self.source / 'web' / 'dist'
        literal(self.dist, missing=True)
        diagnostic.enter('constructor-import-backup-paths')
        self.import_file = args.import_file or self.home / f'coding-{args.org}-v1-import.json'
        literal(self.import_file, missing=True)
        diagnostic.paths['original_import_candidate'] = str(self.import_file)
        for root in (self.source, *(Path(r) for r in registered['registered'])):
            if self.import_file.is_relative_to(root):
                raise Hold('original import file must be outside affected source/runtime trees')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        self.backup = args.backup_dir or self.home / 'operator-backups' / f'coding-{args.org}-{stamp}-{secrets.token_hex(6)}'
        literal(self.backup, missing=True)
        diagnostic.paths['backup_candidate_complete_only_with_manifest'] = str(self.backup)
        if self.backup.exists():
            raise Hold(f'backup destination already exists: {self.backup}')
        for root in (self.source, *(Path(r) for r in registered['registered'])):
            if self.backup.is_relative_to(root) or root.is_relative_to(self.backup):
                raise Hold(f'backup overlaps affected source/runtime: {root}')
        if self.import_file.is_relative_to(self.backup) or self.backup.is_relative_to(self.import_file):
            raise Hold('import file overlaps recovery destination')
        diagnostic.accepted('constructor')

    @staticmethod
    def tool(name):
        path = shutil.which(name)
        if path is None:
            raise Hold(f'{name} unavailable; original toolchain owner must supply the documented executable')
        p = Path(path)
        # Toolchain links are expected; freeze literal link and resolved target.
        target = p.resolve(strict=True)
        if not target.is_file() or not os.access(target, os.X_OK):
            raise Hold(f'invalid {name} executable: {p}')
        return {'path': str(p), 'target': str(target), 'identity': identity(target),
                'sha256': Coordinator.hash_file(target)}

    def python_identity(self):
        literal(self.source / '.venv/bin')
        target = self.python.resolve(strict=True)
        return {'path': str(self.python), 'link': os.readlink(self.python) if self.python.is_symlink() else None,
                'target': str(target), 'identity': identity(target), 'sha256': self.hash_file(target),
                'environment_identity': identity(self.source / '.venv')[:4],
                'pyvenv': digest(read_file(self.source / '.venv/pyvenv.cfg')), 'version': sys.version}

    def command(self, argv, *, cwd=None, accept=(0,), private=False, effect=None):
        """Persist intent before launch and bounded observed results before return."""
        import tempfile
        self.diagnostic.command = {'state': 'not-launched', 'exit': None, 'launch_observed': False,
                                   'effect': effect}
        lifecycle = 'scripts/daemon.sh' in argv
        if lifecycle and not self.receipts:
            raise Hold('durable pre-stop evidence unavailable; no lifecycle effect')
        intent = {'argv': [str(x) for x in argv], 'cwd': str(cwd or self.source),
                  'requested_at': datetime.now(timezone.utc).isoformat(),
                  'last_accepted_layout': self.last_layout}
        if lifecycle and argv[-1] == 'start':
            intent['start_floor'] = self.start_floor
        if lifecycle and hasattr(self, 'original_process'):
            intent['original_daemon_process'] = self.original_process
        prefix = None
        if self.receipts:
            self.counter += 1
            prefix = self.receipts / f'command-{self.counter:03d}'
            self.diagnostic.paths['current_command_candidate'] = str(prefix)
            # An intent without a result is deliberately unresolved. Failure to
            # durably save intent prevents Popen, including the original stop.
            self.diagnostic.effect('command-receipt', 'intent-write-attempted')
            self.diagnostic.observe('command-intent')
            exclusive(prefix.with_suffix('.intent.json'), canonical(intent))
            self.diagnostic.references['last_command_intent'] = str(prefix.with_suffix('.intent.json'))
            self.diagnostic.effect('command-receipt', 'intent-durable')
        child, failure, cleanup_failure = None, None, None
        stdout, stderr = b'', b''
        sizes, exit_code, reason = None, None, None
        self.diagnostic.effect('command-capture', 'temporary-custody-attempted')
        self.diagnostic.observe('command-capture')
        self.diagnostic.command['state'] = 'capture-attempted'
        try:
            with tempfile.TemporaryFile(dir=self.receipts) as out, tempfile.TemporaryFile(dir=self.receipts) as err:
                try:
                    self.diagnostic.command['state'] = 'launch-attempted-effect-uncertain'
                    self.diagnostic.observe('command-launch')
                    if effect is not None:
                        self.diagnostic.effect(effect, 'attempted-effect-uncertain')
                    child = subprocess.Popen(argv, cwd=cwd or self.source, stdout=out, stderr=err)
                    deadline = time.monotonic() + 1800
                    while child.poll() is None:
                        if max(os.fstat(out.fileno()).st_size, os.fstat(err.fileno()).st_size) > _LIMIT:
                            reason = 'output_limit'
                            break
                        if time.monotonic() > deadline:
                            reason = 'timeout'
                            break
                        time.sleep(.1)
                except BaseException as exc:
                    failure = exc
                    reason = 'interrupted' if isinstance(exc, KeyboardInterrupt) else 'subprocess_observation_failed'
                finally:
                    if child is not None:
                        try:
                            if child.poll() is None:
                                child.terminate()
                                try:
                                    child.wait(timeout=10)
                                except subprocess.TimeoutExpired:
                                    child.kill()
                                    child.wait(timeout=10)
                            exit_code = child.returncode
                        except BaseException as exc:
                            cleanup_failure = type(exc).__name__
                            reason = reason or 'child_closure_unobserved'
                    sizes = [os.fstat(stream.fileno()).st_size for stream in (out, err)]
                    out.seek(0)
                    err.seek(0)
                    stdout, stderr = out.read(_LIMIT), err.read(_LIMIT)
        except BaseException as exc:
            failure = failure or exc
            reason = reason or ('interrupted' if isinstance(exc, KeyboardInterrupt) else 'command_capture_failed')
        complete = sizes == [len(stdout), len(stderr)]
        if not complete:
            reason = reason or 'output_capture_incomplete'
        result = dict(intent, finished_observation_at=datetime.now(timezone.utc).isoformat(),
                      exit=exit_code, exit_observed=exit_code is not None,
                      launch_observed=child is not None, child_pid=child.pid if child is not None else None,
                      stdout_digest=digest(stdout), stderr_digest=digest(stderr),
                      captured_bytes=[len(stdout), len(stderr)], observed_stream_bytes=sizes,
                      streams_complete=complete, uncertain=reason is not None, uncertainty=reason,
                      failure_type=type(failure).__name__ if failure else None, cleanup_failure=cleanup_failure)
        if lifecycle:
            # Arbitrary daemon.log tails and private stderr stay in original
            # custody. Even a known stdout must not cause raw stderr to leak.
            result['original_log'] = str(self.home / 'daemon.log')
            safe = stdout in (b'daemon stopped\n', b'not running\n', b'daemon not running\n', b'daemon force-killed\n')
            safe = safe or re.fullmatch(rb'(daemon started|running) \(pid [1-9][0-9]*, port [1-9][0-9]*\)\n', stdout) is not None
            if complete and safe:
                result['stdout'] = stdout.decode('utf8')
        elif private and complete:
            result.update(stdout=stdout.decode('utf8', errors='replace'), stderr=stderr.decode('utf8', errors='replace'))
        self.commands.append(result)
        failed = reason is not None or exit_code not in accept
        if effect is not None and self.diagnostic.effects[effect] != 'not-attempted':
            self.diagnostic.effect(effect, 'command-failed-effect-uncertain' if failed else 'command-returned-stage-not-accepted')
        self.diagnostic.command = {'state': 'failed-or-uncertain' if failed else 'command-returned-not-stage-acceptance',
                                   'exit': exit_code, 'launch_observed': child is not None,
                                   'effect': effect,
                                   'uncertainty': reason, 'stdout_digest': digest(stdout), 'stderr_digest': digest(stderr),
                                   'failure_type': self.diagnostic.token(type(failure).__name__) if failure else None,
                                   'cleanup_failure': cleanup_failure}
        original = Hold('original command failed or is uncertain; preserve command intent/result', category='command_failed') if failed else None
        try:
            if prefix:
                self.diagnostic.observe('command-result-persistence')
                self.diagnostic.effect('command-receipt', 'result-write-attempted')
                exclusive(prefix.with_suffix('.json'), canonical(result))
                self.diagnostic.references['last_command_result'] = str(prefix.with_suffix('.json'))
                if private and not lifecycle and complete:
                    exclusive(prefix.with_suffix('.stdout'), stdout)
                    exclusive(prefix.with_suffix('.stderr'), stderr)
                self.diagnostic.effect('command-receipt', 'result-durable')
        except BaseException as persistence:
            self.diagnostic.effect('command-receipt', 'result-persistence-failed-or-partial')
            if original is not None:
                self.diagnostic.secondary.append('command_result_persistence:' + self.diagnostic.token(type(persistence).__name__))
                raise original from None
            raise
        if original is not None:
            raise original from None
        return exit_code, stdout.decode('utf8', errors='strict'), stderr.decode('utf8', errors='strict')

    def prepare_source(self):
        git = self.tools['git']['path']
        reference_names = ('.git', 'uv.lock', 'pyproject.toml', 'web/package-lock.json', 'web/package.json',
                           'scripts/setup_coding.py', 'scripts/_coding_setup_preflight.py', 'scripts/daemon.sh', 'scripts/build_web.sh')
        rows = [{'source': str(self.source / name), 'metadata': _metadata((self.source / name).lstat()),
                 'parents': _parents(self.source / name)} for name in reference_names]
        if not hasattr(self, '_source_reference_answers'):
            self._role_answers = []
            answer = _custody_answers(self, 'reviewed-public-git-and-source-references', rows, self.source)
            if answer['exceptions']:
                raise Hold('private/unknown reference exception; no source reference content access')
            self._source_reference_answers = {'answer': answer, 'objects': rows}
        else:
            old = self._source_reference_answers['objects']
            # Git may update internal metadata through original commands, but
            # the literal Git boundary and all selected public files stay pinned.
            if any(a['source'] != b['source'] or a['parents'] != b['parents']
                   or (a['metadata'][:5] if a['source'].endswith('/.git') else a['metadata'])
                   != (b['metadata'][:5] if b['source'].endswith('/.git') else b['metadata'])
                   for a, b in zip(old, rows)):
                raise Hold('current admitted source reference custody changed')
        def get(*args):
            code, out, err = self.command([git, '--no-optional-locks', *args])
            if err:
                raise Hold('unexpected source inspection diagnostic')
            return out
        head = get('rev-parse', '--verify', 'HEAD').strip()
        tree = get('rev-parse', 'HEAD^{tree}').strip()
        if not hasattr(self, 'source_pin'):
            self.confirm('This exact selected public source is previously reviewed: HEAD=' + head + ', tree=' + tree + '. I know its current Git/source role; tracked membership alone is not credential admission.')
        self.command([git, 'merge-base', '--is-ancestor', _COMPATIBLE, head])
        # Exclude .codegraph before Git's untracked walk, rather than filtering
        # its output after Git has already traversed the unrelated subtree.
        status = get('status', '--porcelain=v1', '--untracked-files=no')
        untracked = get('ls-files', '--others', '--directory', '--exclude-standard', '--exclude=.codegraph/')
        if status or untracked or get('ls-files', '--', '.codegraph'):
            raise Hold('source has tracked changes or unexpected untracked files; preserve them for original owner')
        if (self.source / '.codegraph').is_symlink():
            raise Hold('.codegraph must be the unrelated literal directory, never an alias')
        codegraph = self.source / '.codegraph'
        if codegraph.exists() and not codegraph.is_dir():
            raise Hold('.codegraph is not the allowed unrelated directory')
        return {'head': head, 'tree': tree, 'status': status, 'root_identity': identity(self.source)[:4],
                'codegraph': identity(codegraph) if codegraph.exists() else None,
                'locks': {str(Path(r['source']).relative_to(self.source)): digest(self._source_reference_bytes(r))
                          for r in rows if Path(r['source']).name != '.git'}}

    @staticmethod
    def _source_reference_bytes(row):
        # Reference role/custody was answered before these bytes are used.
        with _pinned_reader(Path(row['source']), row['metadata'], row['parents']) as stream:
            raw = stream.read(_LIMIT + 1)
        if len(raw) > _LIMIT:
            raise Hold('selected source reference exceeds 1MiB')
        return raw

    def _git_blob(self, oid, work):
        work.check()
        git = self.tools['git']['path']
        _, size_text, err = self.command([git, 'cat-file', '-s', oid])
        if err or re.fullmatch(r'[0-9]+\n', size_text) is None:
            raise Hold('pinned Git blob size unavailable')
        size = int(size_text)
        if size > 64 * _LIMIT:
            raise Hold('pinned Git blob exceeds selected-member bound')
        # cat-file reads raw immutable objects, without checkout/filter/smudge,
        # candidate imports, network or any package-manager invocation.
        result = subprocess.run([git, 'cat-file', 'blob', oid], cwd=self.source,
                                capture_output=True, timeout=max(0.001, work.deadline - time.monotonic()))
        raw = result.stdout
        algorithm = 'sha1' if len(oid) == 40 else 'sha256'
        if (result.returncode or result.stderr or len(raw) != size
                or hashlib.new(algorithm, b'blob ' + str(size).encode('ascii') + b'\0' + raw).hexdigest() != oid):
            raise Hold('pinned raw Git blob identity/size mismatch')
        work.check()
        return raw

    def revalidate(self, *, offline=None, accepted_target=None):
        if offline is None:
            offline = self.offline
        self.diagnostic.observe('revalidate-host')
        require_human_host()
        if identity(self.home)[:4] != self.home_pin:
            raise Hold('daemon-home identity changed; original host owner must reconcile')
        self.diagnostic.observe('revalidate-source-environment')
        if dict(os.environ) != self.environment or self.prepare_source() != self.source_pin:
            raise Hold('frozen source/environment changed; preserve recovery material and original owners')
        self.diagnostic.observe('revalidate-python')
        if self.python_identity() != self.python_pin:
            raise Hold('selected Python/environment identity changed; original environment owner must reconcile')
        self.diagnostic.observe('revalidate-ui-config')
        if hasattr(self, 'ui') and self.ui_inventory() != self.ui:
            raise Hold('prepared UI artifact moved; original source/UI owner must reconcile')
        config = digest(read_file(self.home / 'config.yaml')) if (self.home / 'config.yaml').exists() else None
        if config != self.config_pin:
            raise Hold('daemon configuration changed; original configuration owner must reconcile')
        for name, pinned in self.tools.items():
            self.diagnostic.observe('revalidate-tool-' + name)
            if self.tool(name) != {k: v for k, v in pinned.items() if k != 'version'}:
                raise Hold(f'frozen executable changed: {name}')
        self.diagnostic.observe('revalidate-maintenance')
        current = observe_maintenance(self.home, self.runtime)
        expected = dict(self.accepted_layouts)
        if accepted_target is not None:
            expected[accepted_target[0]] = accepted_target[1]
        # Layouts may advance ONLY through the individually accepted writer.
        if (current['registry'] != self.frozen['registry'] or current['directories'] != self.frozen['directories']
                or current['runtimes'] != self.frozen['runtimes'] or current['excluded'] != self.frozen['excluded']
                or [(o['path'], o['identity']) for o in current['orgs']] != [(o['path'], o['identity']) for o in self.frozen['orgs']]
                or {o['path']: o['layout'] for o in current['orgs']} != expected):
            raise Hold('runtime/org/database membership or identity changed')
        self.diagnostic.observe('revalidate-processes')
        self.process_boundary(offline=offline)
        if offline:
            if self.stopped_lifecycle() != self.offline_lifecycle:
                raise Hold('current stopped lifecycle drift; preserve last accepted stage')
        elif not hasattr(self, 'started') and self.running_lifecycle() != self.entry_lifecycle:
            raise Hold('original running lifecycle changed; no stop/restart fallback')
        if self.accepted_backup is not None:
            self.require_backup()
        if not offline and hasattr(self, 'started'):
            self.diagnostic.observe('revalidate-started-pair')
            self.revalidate_start()
        if not offline:
            self.diagnostic.observe('revalidate-live-scope')
            self.live_scope()
            if hasattr(self, 'started'):
                self.revalidate_start()
        return current

    def live_scope(self):
        """Require complete current evidence; final selected coverage is later.

        Failed identity views expose only an opening preparation list. They
        cannot supply exclusion, even when that opening list is empty.
        """
        from cli.client.client import OpcClient
        from runtime.infrastructure.memory_collection import _identity_schema
        with OpcClient.from_env() as client:
            if hasattr(self, 'started') and client.base_url != f'http://127.0.0.1:{self.started["port"]}':
                raise Hold('serving client port differs from authenticated daemon; import held')
            health = client.get('/api/v1/health')
            health_body = health.json() if health.status_code == 200 else None
            if not isinstance(health_body, dict) or health_body.get('active_runtime') != str(self.runtime):
                raise Hold('original serving runtime observation unavailable/mismatched')
            native = health_body.get('host_sessions')
            if not isinstance(native, dict) or not isinstance(native.get('backend'), dict):
                raise Hold('native serving containers unavailable', category='serving_observation_unavailable')
            admission, residue = native.get('admission'), native.get('residue')
            if (not isinstance(admission, dict) or not isinstance(residue, dict)
                    or native.get('wired') is not True or native['backend'].get('healthy') is not True
                    or any(type(admission.get(k)) is not int for k in ('active', 'queue_depth', 'admitted_total', 'released_total'))
                    or type(residue.get('survivors_count')) is not int
                    or admission.get('active') != 0 or admission.get('queue_depth') != 0
                    or admission.get('shutdown') is not False or admission.get('head_stall_reason') is not None
                    or admission.get('admitted_total') != admission.get('released_total')
                    or residue.get('admission_blocked') is not False or residue.get('survivors_count') != 0
                    or residue.get('block_reason') is not None):
                raise Hold('original serving native admission/residue/outer observation active or unknown; original host supervisor owner must reconcile')
            response = client.get('/api/v1/orgs')
            expected = {(o['org'], str(Path(o['path']).parent)) for o in self.frozen['orgs'] if o['runtime'] == str(self.runtime)}
            if response.status_code != 200:
                raise Hold('loaded/broken org census unavailable')
            body = response.json()
            if (not isinstance(body, dict) or not isinstance(body.get('orgs'), list)
                    or any(not isinstance(o, dict) or not isinstance(o.get('slug'), str)
                           or not isinstance(o.get('root'), str) for o in body['orgs'])
                    or body.get('broken') != [] or {(o['slug'], o['root']) for o in body['orgs']} != expected):
                raise Hold('loaded/broken/partial org census disagrees with complete registry; original org attachment owner must reconcile')
            observations = {}
            for slug, root in expected:
                response = client.get(f'/api/v1/orgs/{quote(slug, safe="")}/audit', params={'action': 'memory_collection_seal', 'limit': 1})
                if response.status_code != 200:
                    raise Hold(f'{slug}: original serving observer unavailable')
                body = response.json()
                view = body.get('memory_collection_observation') if isinstance(body, dict) else None
                if not isinstance(view, dict):
                    raise Hold('serving response/view container unavailable', category='serving_observation_unavailable')
                preparations = view.get('active_preparations')
                # Match the producer's original nested references predicate.
                # Validate the entire original list before classifying it;
                # neither falsey coercion nor filtering can turn work into idle.
                def references(value: object) -> bool:
                    if not isinstance(value, list):
                        return False
                    for row in value:
                        if (not isinstance(row, dict)
                                or set(row) != {'ordinal', 'task_id', 'agent', 'session_id', 'expectation_known'}
                                or type(row['ordinal']) is not int or row['ordinal'] <= 0
                                or not isinstance(row['task_id'], str) or re.fullmatch(r'TASK-[0-9]{3,}', row['task_id']) is None
                                or not isinstance(row['agent'], str) or not row['agent'].strip()
                                or (row['session_id'] is not None and
                                    (not isinstance(row['session_id'], str) or not row['session_id'].strip()))
                                or type(row['expectation_known']) is not bool):
                            return False
                    return True
                valid = references(preparations)
                self.diagnostic.observations[slug] = {key: view.get(key) for key in
                                                     ('org', 'sampled_at', 'observation_error', 'boot_id', 'data_through')}
                self.diagnostic.checks[slug] = {
                    'preparations': 'invalid' if not valid else 'nonempty' if preparations else 'empty',
                    'observation_error_present': view.get('observation_error') is not None,
                    'boot_present': bool(view.get('boot_id')), 'cutoff_present': bool(view.get('data_through')),
                    'identity_valid': False,
                }
                if not valid:
                    raise Hold('preparation reference list malformed or unavailable', category='preparation_list_invalid')
                self.diagnostic.preparations[slug] = preparations  # exact original, including every row
                cutoff = view.get('data_through')
                try:
                    cutoff_valid = isinstance(cutoff, str) and datetime.fromisoformat(cutoff).tzinfo is not None
                except ValueError:
                    cutoff_valid = False
                if (view.get('observation_error') is not None
                        or not isinstance(view.get('boot_id'), str) or not view['boot_id']
                        or view.get('org') != slug or not cutoff_valid):
                    raise Hold('complete current preparation observation unavailable; Engineering/serving owner must establish a supported running observation or operator positive stopped entry',
                               category='serving_observation_unavailable', details={'org': slug})
                loaded = view.get('installed_identity')
                _identity_schema(loaded)
                self.diagnostic.checks[slug]['identity_valid'] = True
                if preparations:
                    raise Hold('original preparations retain ownership', category='preparations_owned')
                if (view.get('observation_error') is not None or not view.get('boot_id') or not view.get('data_through')
                        or loaded.get('org_root') != root
                        or loaded.get('runtime_root') != str(self.runtime)
                        or (hasattr(self, 'started') and loaded.get('source_root') != str(self.source))):
                    raise Hold(f'{slug}: serving boot/source/native preparation/outer observation unknown; original observer owner must reconcile before any effect')
                observations[slug] = view
            boots = {slug: view['boot_id'] for slug, view in observations.items()}
            if self.boots is not None and boots != self.boots:
                raise Hold('serving boot identity changed outside the accepted original start; original lifecycle owner must reconcile')
            self.boots = boots
            return observations

    def process_boundary(self, *, offline=False):
        rows = checked_processes()
        by_pid = {r.pid: r for r in rows}
        ancestors, current = set(), os.getpid()
        while current > 1:
            if current in ancestors or current not in by_pid:
                raise Hold('current host ancestor census incomplete')
            ancestors.add(current)
            current = by_pid[current].ppid
        # Same-user attribution and all current durable owners supplement the
        # human-controlled cooperative window. Other hosts need that control;
        # neither this census nor a terminal task certifies historical closure.
        for row in rows:
            proc = Path('/proc') / str(row.pid)
            try:
                if proc.stat().st_uid != os.getuid():
                    continue
                cwd = Path(os.readlink(proc / 'cwd'))
                raw = (proc / 'cmdline').read_bytes()
                environment = (proc / 'environ').read_bytes().split(b'\0')
                statline = (proc / 'stat').read_text()
                start = statline[statline.rfind(')') + 2:].split()[19]
            except FileNotFoundError:
                continue
            except OSError:
                raise Hold(f'process {row.pid}: same-user attribution unavailable') from None
            if start != row.start_identity:
                raise Hold(f'process {row.pid}: identity moved')
            if any(any(value.startswith(key.encode() + b'=') for value in environment) for key in _MARKERS):
                raise Hold(f'process {row.pid}: managed task/job/native execution still active; original owner must close it')
            if any(cwd.is_relative_to(Path(r) / 'orgs') for r in self.frozen['registry']['registered']):
                raise Hold(f'process {row.pid}: runtime/org/workspace consumer still active; original process owner must close it')
            if (row.pid not in ancestors and cwd.is_relative_to(self.home)
                    and b'runtime.daemon' not in raw):
                raise Hold(f'process {row.pid}: shared-daemon store consumer still active')
            if offline and b'runtime.daemon' in raw:
                raise Hold(f'process {row.pid}: daemon still active; original lifecycle owner must reconcile')
            if b'runtime.daemon' in raw:
                daemon_env = dict(value.split(b'=', 1) for value in environment if b'=' in value)
                daemon_home = daemon_env.get(b'HAPPYRANCH_DAEMON_HOME') or str(Path.home() / '.happyranch').encode()
                if daemon_home != str(self.home).encode() or cwd != self.source:
                    raise Hold(f'process {row.pid}: serving daemon home/source attribution differs; original host owner must reconcile')
            source_consumer = cwd.is_relative_to(self.source) or str(self.source).encode() in raw
            if source_consumer and row.pid not in ancestors:
                if offline or b'runtime.daemon' not in raw:
                    raise Hold(f'process {row.pid}: selected source/environment consumer still active; original owner must close it')
                if cwd != self.source:
                    raise Hold(f'process {row.pid}: daemon source root is unverified')

    def confirm(self, text):
        self.diagnostic.observe('human-confirmation')
        if input(text + '\nType CONFIRM to continue: ') != 'CONFIRM':
            raise Hold('human confirmation declined; no next effect')

    def initialize_receipts(self) -> None:
        """After host/maintenance confirmation, before any lifecycle effect."""
        self.diagnostic.enter('receipt-custody')
        self.diagnostic.effect('receipt-custody', 'creation-attempted-backup-incomplete')
        if self.backup.parent == self.home / 'operator-backups' and not self.backup.parent.exists():
            self.backup.parent.mkdir(mode=0o700)
        literal(self.backup.parent)
        self.backup.mkdir(mode=0o700)
        if self.backup.lstat().st_uid != os.getuid() or stat.S_IMODE(self.backup.lstat().st_mode) != 0o700:
            raise Hold('exclusive recovery destination custody/mode unavailable')
        receipts = self.backup / 'receipts'
        receipts.mkdir(mode=0o700)
        if receipts.lstat().st_uid != os.getuid() or stat.S_IMODE(receipts.lstat().st_mode) != 0o700:
            raise Hold('private receipt custody/mode unavailable')
        self.receipts = receipts
        self.diagnostic.receipts = receipts
        self.diagnostic.paths['private_receipt_directory_observed'] = str(receipts)
        exclusive(self.backup / 'INCOMPLETE.json', canonical({
            'complete': False, 'phase': 'receipts-only; backup not yet started',
            'source': self.source_pin, 'installation': self.frozen,
            'last_accepted_layout': self.last_layout,
            'recovery': 'preserve intent/results and original logs/owners; no automatic restart/restore/replay'}))
        for directory in (self.home, self.backup.parent):
            fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        self.diagnostic.references['incomplete'] = str(self.backup / 'INCOMPLETE.json')
        self.diagnostic.effect('receipt-custody', 'accepted-receipts-only-backup-incomplete')
        self.diagnostic.accepted('receipt-custody')
        print(f'   Recovery evidence: {self.receipts}; backup INCOMPLETE until verified manifest acceptance')

    def running_lifecycle(self):
        """Select the original live owner before stop, never from PID absence."""
        pins = {name: identity(self.home / name) for name in ('daemon.pid', 'daemon.port')}
        if any(pin[3] != os.getuid() for pin in pins.values()):
            raise Hold('running lifecycle custody unavailable')
        pid_raw, port_raw = (read_file(self.home / name, 64) for name in ('daemon.pid', 'daemon.port'))
        if (re.fullmatch(rb'[1-9][0-9]{0,9}\n?', pid_raw) is None
                or re.fullmatch(rb'[1-9][0-9]{0,4}\n?', port_raw) is None
                or not 1 <= int(port_raw) <= 65535):
            raise Hold('running lifecycle identity malformed')
        pid = int(pid_raw)
        rows = {r.pid: r for r in checked_processes()}
        original = rows.get(pid)
        if original is None or original.start_identity is None:
            raise Hold('stale/unknown current daemon identity; no stopped-entry fallback')
        proc = Path('/proc') / str(pid)
        if (proc.stat().st_uid != os.getuid() or b'runtime.daemon' not in (proc / 'cmdline').read_bytes()
                or Path(os.readlink(proc / 'cwd')) != self.source):
            raise Hold('original daemon process source/custody unavailable')
        selected = (pid, original.start_identity)
        if self.original_process is not None and selected != self.original_process:
            raise Hold('original daemon process replaced; no fallback or stop')
        later = {r.pid: r for r in checked_processes()}.get(pid)
        if (later is None or later.start_identity != original.start_identity
                or {name: identity(self.home / name) for name in pins} != pins
                or read_file(self.home / 'daemon.pid', 64) != pid_raw
                or read_file(self.home / 'daemon.port', 64) != port_raw):
            raise Hold('original running lifecycle moved during observation')
        self.original_process = selected
        return pins, pid_raw, port_raw, selected

    def stopped_lifecycle(self):
        """Positive existing stopped control plus registry, never old shutdown proof.

        Used with repeated complete current owner/process observations and the
        maintained human/cooperative window. No stopped HTTP request or fake
        preparation/stop receipt is manufactured.
        """
        from scripts.migrate_workflow_submission_schema import _lifecycle_snapshot
        for name in ('daemon.pid', 'daemon.port'):
            path = literal(self.home / name, missing=True)
            if path.exists():
                raise Hold('stopped entry requires both original lifecycle files absent; stale/partial files hold')
        observed = _lifecycle_snapshot(self.runtime)
        if not any(row[0] == 'runtimes.yaml' and row[1] is not None for row in observed):
            raise Hold('complete stopped lifecycle/runtime registration unavailable')
        if registry(self.home) != self.frozen['registry']:
            raise Hold('stopped runtime registration moved')
        return observed

    def stop(self):
        self.diagnostic.enter('stop-preflight')
        self.revalidate()
        self.initialize_receipts()
        self.revalidate()
        if not self.entry_running:
            # This branch was independently selected before any stop attempt.
            # Leave the stop effect not-attempted; preserve old stop uncertainty.
            self.diagnostic.accepted('positive-stopped-entry; stop-not-attempted')
            return
        self.diagnostic.enter('stop-original-process')
        if self.running_lifecycle() != self.entry_lifecycle:
            raise Hold('original running lifecycle moved before stop')
        pid = self.original_process[0]
        self.diagnostic.enter('stop-command')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'stop', '--force'], private=True, effect='stop')
        self.diagnostic.enter('stop-closure')
        if out != 'daemon stopped\n' or err:
            raise Hold('stop was stale, force-killed or ambiguous; original host/native owner must reconcile; no automatic restart')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'status'], accept=(1,), private=True)
        if out != 'not running\n' or err or pid in {r.pid for r in checked_processes()}:
            raise Hold('original daemon stop closure unavailable; no preparation/migration/restart')
        self.offline_lifecycle = self.stopped_lifecycle()
        self.revalidate(offline=True)
        self.offline = True  # Only successful original closure can change phase.
        self.diagnostic.effect('stop', 'accepted-original-closure')
        self.diagnostic.accepted('stop')

    def _discover_backup(self, roots, excluded, reserved, work):
        """Metadata only, with exclusions applied before descent or alias use."""
        graph = {}
        def visit(path, root):
            work.check(entries=1)
            if path == self.backup or path == self.home / 'operator-backups':
                return
            if path != root and path in roots:
                return
            info = path.lstat()
            row = {'source': str(path), 'root': str(root), 'metadata': _metadata(info),
                   'parents': _parents(path), 'identity': list(identity_or_link(path)),
                   'size': info.st_size, 'mtime_ns': info.st_mtime_ns, 'ctime_ns': info.st_ctime_ns}
            graph[str(path)] = row
            if path in reserved:
                if identity(path) != reserved[path]:
                    raise Hold('untouched reserved boundary moved; no traversal or copying')
                row.update(excluded=True, disposition='untouched migration exclusion; boundary only; members not traversed or backed up')
                return
            if info.st_dev != root.lstat().st_dev or info.st_uid != os.getuid():
                raise Hold('unaccounted affected mount or unavailable custody', details={'path': str(path)})
            if path in excluded and stat.S_ISDIR(info.st_mode):
                row.update(excluded=True, disposition='unaffected subtree; not traversed')
                return
            if stat.S_ISLNK(info.st_mode):
                if (not path.is_relative_to(self.source / '.venv')
                        and not path.is_relative_to(self.source / 'web/node_modules')
                        and path != self.source / 'CLAUDE.md'):
                    raise Hold('unaccounted backup alias', details={'path': str(path)})
                row['link'] = os.readlink(path)
                if path == self.source / 'CLAUDE.md' and row['link'] != 'AGENTS.md':
                    raise Hold('only raw CLAUDE to AGENTS alias is supported')
                resolved, links = _backup_link_closure(path, work)
                # Metadata navigation is never content permission; all hops and
                # the final literal referent are separately closed below.
                row['link_closure'] = links
                row.update(referent=str(resolved), referent_metadata=_metadata(resolved.lstat()), referent_parents=_parents(resolved),
                           referent_identity=identity(resolved), restore=str(path), kind='symlink')
            elif stat.S_ISDIR(info.st_mode):
                row.update(kind='directory', restore=str(path))
                before = sorted(p.name for p in path.iterdir())
                row['members'] = before
                for name in before:
                    visit(path / name, root)
                if sorted(p.name for p in path.iterdir()) != before:
                    raise Hold('backup directory membership moved', details={'path': str(path)})
            elif not stat.S_ISREG(info.st_mode):
                raise Hold('unaccounted affected special file', details={'path': str(path)})
            if _metadata(path.lstat()) != row['metadata']:
                raise Hold('backup object moved during metadata discovery', details={'path': str(path)})
        for root in roots:
            visit(root, root)
        work.check()
        return graph

    def backup_inventory(self):
        """Whole OLD recovery, from metadata admission through original copies."""
        self.diagnostic.enter('backup-preflight')
        self.revalidate(offline=True)
        if self.receipts != self.backup / 'receipts':
            raise Hold('pre-stop recovery evidence unavailable; no backup/preparation')
        self._role_answers = []
        inventory = {'complete': False, 'source': self.source_pin, 'installation': self.frozen,
                     'tools': self.tools, 'python': self.python_pin, 'commands_before_backup': self.commands[:],
                     'files': [], 'references': [], 'current_custody': self._role_answers,
                     'source_reference_custody': self._source_reference_answers,
                     'restore': 'manual original-owner recovery of selected paths/content using independent replacement inodes; never write through surviving shared links; no external topology promise; preserve credential custody; pre-J code must never boot J'}
        self.diagnostic.enter('backup-metadata-discovery')
        self.diagnostic.effect('backup', 'attempted-incomplete')
        exclusive(self.backup / 'BACKUP-STARTED.json', canonical(inventory))
        dest = self.backup / 'files'
        dest.mkdir(mode=0o700)
        roots = list(dict.fromkeys([self.home, *(Path(r) for r in self.frozen['registry']['registered']), self.source]))
        excluded = {self.source / '.codegraph', self.source / '.git', self.source / '.claude/worktrees'}
        for root in roots:
            excluded.update((root / 'task-tmp', root / 'scratch'))
        excluded.update(Path(o['path']).parent / 'workspaces' for o in self.frozen['orgs'])
        reserved = {Path(path): pin for path, pin in self.frozen['excluded']}
        excluded.update(reserved)
        work = _OriginWork()
        origins = None
        try:
            graph = self._discover_backup(roots, excluded, reserved, work)
            discovery = _discovery_digest(graph)
            inventory['discovery_sha256'] = discovery
            inventory['files'] = list(graph.values())
            for index, row in enumerate(graph.values()):
                row['slot'] = f'{index:06d}'
            origins = _PublicOrigins(self, graph, inventory, work)
            self.diagnostic.observe('backup-current-custodian-admission')
            origins.admit_graph()
            # No content read/copy occurs during discovery or role collection.
            # This catches membership and alias movement after a genuine answer.
            if _discovery_digest(self._discover_backup(roots, excluded, reserved, work)) != discovery:
                raise Hold('complete metadata membership moved after custody answers; no new scope or retry')
            self.diagnostic.observe('backup-independent-public-expectations')
            origins.prepare_inputs()
            origins.build_expectations()
            exclusive(self.backup / 'recovery-plan.json', canonical(inventory))
            inventory['plan'] = self._retained_pin(self.backup / 'recovery-plan.json', work)
            self.diagnostic.observe('backup-admitted-original-copies')
            for row in graph.values():
                work.check()
                if row.get('excluded') or row.get('kind') in ('directory', 'symlink') or row.get('role') == 'private-untouched':
                    continue
                path = Path(row['source'])
                if str(path).endswith(('-wal', '-shm', '-journal')):
                    main = Path(re.sub(r'-(wal|shm|journal)$', '', str(path)))
                    parent = graph.get(str(main))
                    if parent is None or main.suffix not in ('.db', '.sqlite3', '.sqlite') or parent.get('excluded'):
                        raise Hold('unaccounted SQLite companion', details={'path': str(path)})
                    row.update(disposition='SQLite companion retained in place; committed state in SQLite backup', sqlite_source=str(main))
                elif path.suffix in ('.db', '.sqlite3', '.sqlite'):
                    # The original WAL-consistent SQLite/integrity/FK owner is
                    # unchanged; credentials are never disguised as a database.
                    if _public_suspicion(path):
                        raise Hold('suspect database has no public byte-origin admission', details={'path': str(path)})
                    target = dest / row['slot']
                    self.backup_database(path, target)
                    row.update(kind='sqlite-backup-including-committed-WAL', integrity_check='ok', foreign_key_violations=0,
                               backup=str(target), restore=str(path), sha256=self.hash_file(target, private=True, work=work),
                               backup_metadata=_metadata(target.lstat()), backup_parents=_parents(target))
                else:
                    origins.copy_row(row)
            self.revalidate(offline=True)
            # Closing coverage is a full rediscovery, not only the rows copied.
            if _discovery_digest(self._discover_backup(roots, excluded, reserved, work)) != discovery:
                raise Hold('closing full original membership/metadata/alias/link coverage changed')
            for row in graph.values():
                if row.get('excluded') or row.get('kind') == 'directory' or row.get('role') == 'private-untouched':
                    continue
                if row.get('kind') == 'symlink':
                    coverage = row.get('referent_coverage', {})
                    if coverage.get('kind') == 'inventoried-original':
                        target = graph[coverage['source']]
                        if target.get('kind') != 'directory' and 'backup' not in target:
                            raise Hold('allowed referent lacks original recovery copy', details={'path': row['source']})
                elif row.get('sqlite_source'):
                    if graph[row['sqlite_source']].get('kind') != 'sqlite-backup-including-committed-WAL':
                        raise Hold('SQLite companion has no complete original backup')
                elif 'backup' not in row:
                    raise Hold('required affected original has no recovery copy', details={'path': row['source']})
            inventory['complete'] = True
            inventory['coverage'] = {'required_members': len(graph), 'discovery_sha256': discovery,
                                     'selected_content_paths_only': True, 'restore_exercised': False}
            # All copies/reference inputs/plan and their exact directory
            # membership are authenticated before exclusive manifest publication.
            self._verify_recovery(inventory, work)
            for directory in (dest, origins.directory):
                fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
            self.diagnostic.observe('backup-complete-manifest')
            raw = canonical(inventory)
            manifest = self.backup / 'manifest.json'
            exclusive(manifest, raw)
            accepted = {'manifest': self._retained_pin(manifest, work), 'digest': digest(raw), 'size': len(raw),
                        'backup': identity(self.backup)[:4], 'receipts': identity(self.receipts)[:4],
                        'files': identity(dest)[:4], 'origins': identity(origins.directory)[:4],
                        'inventory': inventory, 'work': work}
            self.revalidate(offline=True)
            work.check()
            self.accepted_backup = accepted
            self.require_backup()
        except BaseException as original:
            self.accepted_backup = None
            inventory['complete'] = False
            inventory['first_failure'] = {'stage': self.diagnostic.stage, 'observation': self.diagnostic.observation,
                                          'type': self.diagnostic.token(type(original).__name__),
                                          'hold_details': original.details if isinstance(original, Hold) else None}
            try:
                exclusive(self.backup / 'FAILED.json', canonical(inventory))
            except BaseException as persistence:
                self.diagnostic.secondary.append('backup_failure_persistence:' + self.diagnostic.token(type(persistence).__name__))
                original.add_note('partial backup manifest publication also failed; retain INCOMPLETE.json and all original/copy files')
            raise
        finally:
            if origins is not None:
                origins.close()
        self.diagnostic.references['manifest'] = str(self.backup / 'manifest.json')
        self.diagnostic.effect('backup', 'accepted-complete-manifest-restore-unverified')
        self.diagnostic.accepted('backup')
        print(f'3. Backup complete: {self.backup}/manifest.json (restore NOT RUN; no automatic rollback)')

    def _retained_pin(self, path, work):
        return {'path': str(path), 'metadata': _metadata(path.lstat()), 'parents': _parents(path),
                'size': path.lstat().st_size, 'sha256': self.hash_file(path, private=True, work=work)}

    def _verify_retained(self, row, work):
        path = Path(row['path'])
        if self.hash_file(path, pin=row['metadata'], parents=row['parents'], private=True, work=work) != row['sha256']:
            raise Hold('retained old recovery material moved or changed', details={'path': str(path)})

    def _verify_recovery(self, inventory, work):
        """Authenticate retained OLD custody, never compare a replaced new env."""
        work.check()
        if inventory.get('complete') is not True or not inventory.get('coverage'):
            raise Hold('incomplete original recovery coverage')
        expected = set()
        for row in inventory['files']:
            work.check()
            if row.get('role') == 'private-untouched':
                path = Path(row['source'])
                literal(path)
                if _metadata(path.lstat()) != row['metadata'] or _parents(path) != row['parents']:
                    raise Hold('original untouched credential custody moved; never regenerate or repair', details={'path': str(path)})
            coverage = row.get('referent_coverage', {})
            if coverage.get('kind') == 'untouched-selected-base-interpreter':
                for link in coverage['external_links']:
                    path = Path(link['path'])
                    if (_metadata(path.lstat()) != link['metadata'] or _parents(path) != link['parents']
                            or os.readlink(path) != link['link']):
                        raise Hold('untouched indispensable external link chain moved', details={'path': str(path)})
                path = Path(row['referent'])
                if (self.hash_file(path, pin=row['referent_metadata'], parents=row['referent_parents'], work=work)
                        != coverage['identity']['sha256']):
                    raise Hold('untouched indispensable interpreter custody changed')
            if 'backup' in row:
                expected.add(Path(row['backup']).name)
                self._verify_retained({'path': row['backup'], 'metadata': row['backup_metadata'],
                                       'parents': row['backup_parents'], 'sha256': row['sha256']}, work)
        if set(p.name for p in (self.backup / 'files').iterdir()) != expected:
            raise Hold('retained OLD copy membership changed')
        expected = set()
        for row in inventory['references']:
            expected.add(Path(row['path']).name)
            self._verify_retained(row, work)
        if set(p.name for p in (self.backup / 'origins').iterdir()) != expected:
            raise Hold('retained reference membership changed')
        self._verify_retained(inventory['plan'], work)
        if 'public_input_instruction' in inventory:
            self._verify_retained(inventory['public_input_instruction'], work)
        inputs = inventory.get('archive_input')
        if inputs:
            directory = Path(inputs['path'])
            _private_input_directory(directory)
            if (_metadata(directory.lstat()) != inputs['metadata'] or _parents(directory) != inputs['parents']
                    or tuple(sorted(p.name for p in directory.iterdir())) != tuple(inputs['members'])):
                raise Hold('original independent archive input custody/membership changed')
            for row in inputs['files']:
                if self.hash_file(Path(row['source']), pin=row['metadata'], parents=row['parents'], private=True, work=work) != row['sha256']:
                    raise Hold('original public archive reference changed')
        work.check()

    def require_backup(self):
        accepted = self.accepted_backup
        try:
            if accepted is None:
                raise Hold('no verified complete backup accepted in this invocation')
            for directory, key in ((self.backup, 'backup'), (self.receipts, 'receipts'),
                                   (self.backup / 'files', 'files'), (self.backup / 'origins', 'origins')):
                literal(directory)
                if identity(directory)[:4] != accepted[key] or stat.S_IMODE(directory.lstat().st_mode) != 0o700:
                    raise Hold('accepted recovery directory custody changed; no next effect')
            self._verify_retained(accepted['manifest'], accepted['work'])
            # Authenticate the exact in-memory owner rows as well as durable
            # bytes. No caller-supplied flag or old-invocation manifest is used.
            if digest(canonical(accepted['inventory'])) != accepted['digest']:
                raise Hold('invocation-local accepted recovery rows changed')
            self._verify_recovery(accepted['inventory'], accepted['work'])
        except BaseException:
            self.accepted_backup = None
            raise

    @staticmethod
    def hash_file(path, *, pin=None, parents=None, private=False, work=None):
        if pin is None and parents is None and not private and work is None:
            # Preserve original trusted tool/proc-executable/UI callers. Only
            # admitted recovery sources use the descriptor/custody contract.
            h = hashlib.sha256()
            with path.open('rb') as stream:
                while chunk := stream.read(_LIMIT):
                    h.update(chunk)
            return h.hexdigest()
        if pin is None:
            pin = _metadata(path.lstat())
        if parents is None:
            parents = _parents(path)
        h = hashlib.sha256()
        with _pinned_reader(path, pin, parents, private=private) as stream:
            while chunk := stream.read(_LIMIT):
                if work is not None:
                    work.check()
                h.update(chunk)
        if work is not None:
            work.check()
        return h.hexdigest()

    @staticmethod
    def copy_regular(path, target, *, pin, parents, expected=None, work):
        # All callers supply prior positive role admission, not just metadata.
        # For a suspect slot independent expectations exist BEFORE this open.
        variants = expected if isinstance(expected, list) else [expected] if expected is not None else None
        with _pinned_reader(path, pin, parents) as source:
            if variants is not None:
                size = max(len(value) for value in variants)
                raw = source.read(size + 1)
                work.check()
                if not any(raw == value for value in variants):
                    raise Hold('actual ORIGINAL bytes disagree with independent public origin; no repair or reference substitution', details={'path': str(path)})
                source.seek(0)
            # Target parents are held literally, and readback proves private
            # independent inode custody. No hardlink/reflink/source repair.
            with _literal_output(target) as output:
                original_hash = hashlib.sha256()
                while chunk := source.read(_LIMIT):
                    work.check()
                    original_hash.update(chunk)
                    if output.write(chunk) != len(chunk):
                        raise Hold('original recovery short write', details={'path': str(target)})
                output.flush()
                os.fsync(output.fileno())
                if _metadata(os.fstat(source.fileno())) != pin:
                    raise Hold('original changed while copying', details={'path': str(path)})
                if variants is not None and original_hash.hexdigest() != digest(raw):
                    raise Hold('original changed between equality and copy', details={'path': str(path)})
            if Coordinator.hash_file(target, private=True, work=work) != original_hash.hexdigest():
                raise Hold('original copy readback mismatch', details={'path': str(target)})
        work.check()

    @staticmethod
    def backup_database(path, target):
        from scripts._coding_setup_preflight import Hold, read_database
        import time
        deadline = time.monotonic() + 120
        def progress(status, remaining, total):
            if time.monotonic() > deadline or total > 2000000:
                raise Hold(f'complete SQLite backup budget exhausted: {path}; preserve incomplete copy')
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
        with read_database(path) as source:
            destination = sqlite3.connect(target)
            try:
                source.backup(destination, pages=256, progress=progress)
                if [r[0] for r in destination.execute('PRAGMA integrity_check')] != ['ok'] or destination.execute('PRAGMA foreign_key_check').fetchone():
                    raise Hold(f'backup integrity/FK failed: {path}')
            finally:
                destination.close()
        with target.open('rb') as f:
            os.fsync(f.fileno())

    def prepare(self):
        self.diagnostic.enter('preparation-preflight')
        print('4. Preparing locked source environment and matching UI')
        self.revalidate(offline=True)
        self.require_backup()
        self.diagnostic.enter('preparation-uv')
        self.command([self.tools['uv']['path'], 'sync', '--locked', '--no-python-downloads', '--python', str(self.python),
                      '--cache-dir', str(self.backup / 'preparation-uv-cache')], effect='preparation-uv')
        self.diagnostic.effect('preparation-uv', 'command-accepted-environment-may-have-changed')
        self.diagnostic.accepted('preparation-uv-command')
        self.diagnostic.enter('preparation-python-origins')
        prepared_python = self.python_identity()
        if any(prepared_python[key] != self.python_pin[key] for key in ('target', 'sha256', 'version')):
            raise Hold('uv changed the selected base compiler; preserve OLD recovery, no next effect')
        # OLD .venv/pyvenv pins remain in accepted recovery. This separately
        # records the actual legitimately prepared environment for later stages.
        self.python_pin = prepared_python
        self.revalidate(offline=True)
        code, version, err = self.command([str(self.python), '--version'])
        if err or re.fullmatch(r'Python 3\.(12|13|14)\.[0-9]+\n', version) is None:
            raise Hold('prepared Python is outside the selected supported range')
        # Check actual selected module origins in the resulting environment,
        # rather than inferring them from an executable name or uv exit0.
        code, origins, err = self.command([str(self.python), '-c',
            'import json,sys,runtime.infrastructure.workflow_schema as w,cli.main as c; '
            'print(json.dumps([sys.executable,w.__file__,c.__file__]))'])
        if err or json.loads(origins) != [str(self.python), str(self.source / 'runtime/infrastructure/workflow_schema.py'), str(self.source / 'cli/main.py')]:
            raise Hold('prepared Python/source module origins differ from selected source')
        code, version, err = self.command([self.tools['node']['path'], '--version'])
        if not re.fullmatch(r'v24\.[0-9]+\.[0-9]+\n', version) or err:
            raise Hold('Node 24 required; no alternate toolchain download')
        self.diagnostic.enter('preparation-npm')
        self.require_backup()
        self.command([self.tools['npm']['path'], 'ci', '--cache', str(self.backup / 'preparation-npm-cache')], cwd=self.source / 'web', effect='preparation-npm')
        self.diagnostic.effect('preparation-npm', 'command-accepted-dependencies-may-have-changed')
        self.diagnostic.accepted('preparation-npm-command')
        self.revalidate(offline=True)
        self.diagnostic.enter('preparation-ui-build')
        self.require_backup()
        self.command([self.tools['bash']['path'], 'scripts/build_web.sh', *(['--production'] if self.args.ui_profile == 'production' else [])], effect='preparation-ui')
        self.diagnostic.enter('preparation-ui-observation')
        from runtime.daemon.routes.web_static import _resolve_dist_dir
        if _resolve_dist_dir(self.settings) != self.dist:
            raise Hold('built UI is not the selected served artifact')
        self.ui = self.ui_inventory()
        self.revalidate(offline=True)
        exclusive(self.receipts / 'prepared-ui.json', canonical(self.ui))
        self.diagnostic.references['prepared_ui'] = str(self.receipts / 'prepared-ui.json')
        self.diagnostic.effect('preparation-ui', 'accepted-artifact-receipt')
        self.diagnostic.accepted('preparation')

    def ui_inventory(self):
        raw = read_file(self.dist / 'index.html')
        if b'HappyRanch UI is not built' in raw:
            raise Hold('UI is the source placeholder')
        assets = re.findall(rb'(?:src|href)=["\'](/assets/[^"\']+)["\']', raw)
        if not assets:
            raise Hold('UI index has no matching built assets')
        result = {'index.html': digest(raw)}
        for asset in assets:
            name = asset.decode('utf8').lstrip('/')
            if '..' in Path(name).parts or '?' in name or '#' in name:
                raise Hold('unsupported UI asset reference')
            result[name] = self.hash_file(literal(self.dist / name))
        return result

    def run_transition(self, source_layouts, target_layout, script, arguments, needed_exit, needed_text):
        self.diagnostic.enter('migration-' + target_layout + '-preflight')
        self.require_backup()
        current = self.revalidate(offline=True)
        layout = next(o['layout'] for o in current['orgs'] if o['path'] == self.target['path'])
        self.last_layout = layout
        self.diagnostic.layout = layout
        if layout not in source_layouts:
            if 'FEGHIJ'.index(layout) >= 'FEGHIJ'.index(target_layout):
                return  # whole current readers accepted; no earlier writer
            raise Hold(f'unsupported transition {layout} to {target_layout}')
        print(f'5. Checking {layout} → {target_layout}')
        argv = [str(self.python), f'scripts/{script}', *arguments]
        self.diagnostic.enter('migration-' + target_layout + '-needed-check')
        code, out, err = self.command([*argv, '--check'], accept=(0, 1, 3), private=True)
        if code != needed_exit or out != needed_text + '\n' or err:
            raise Hold(f'{script} check exit={code}: unexpected/refused result; no writer; original migration owner must reconcile')
        self.revalidate(offline=True)
        self.diagnostic.enter('migration-' + target_layout + '-writer')
        self.command([*argv, *(['--confirm-cooperative-offline'] if target_layout == 'J' else [])], private=True, effect='migration-' + target_layout)
        self.diagnostic.enter('migration-' + target_layout + '-post-check')
        code, out, err = self.command([*argv, '--check'], private=True)
        expected = (f'ready: org {self.args.org}; layout G; compatible reader required\n' if target_layout == 'G' else
                    'already-compatible: empty coding layout J; J-compatible reader required\n' if target_layout == 'J' else
                    f'already-compatible: layout {target_layout}; compatible reader required\n')
        if out != expected or err:
            raise Hold(f'{script}: original post-check unexpected; preserve last layout {self.last_layout}, no next writer/start')
        current = self.revalidate(offline=True, accepted_target=(self.target['path'], target_layout))
        actual = next(o['layout'] for o in current['orgs'] if o['path'] == self.target['path'])
        if actual != target_layout:
            raise Hold(f'{script}: whole target mismatch {actual}; no next writer/start')
        self.accepted_layouts[self.target['path']] = target_layout
        self.last_layout = target_layout
        self.diagnostic.layout = target_layout
        self.diagnostic.effect('migration-' + target_layout, 'accepted-complete-target')
        self.diagnostic.accepted('migration-' + target_layout)

    def migrate(self):
        from runtime.infrastructure.workflow_schema import (submission_migration_guidance,
            author_handoff_migration_guidance, revision_migration_guidance, coding_migration_guidance)
        args = ['--runtime-root', str(self.runtime), '--org', self.args.org]
        org_args = ['--org-root', str(Path(self.target['path']).parent), '--org-slug', self.args.org]
        self.run_transition(('F', 'E'), 'G', 'migrate_workflow_submission_schema.py', args, 3,
                            'migration-needed: ' + submission_migration_guidance(org_slug=self.args.org, runtime_root=str(self.runtime)))
        self.run_transition(('G',), 'H', 'migrate_workflow_author_handoff_schema.py', org_args, 1,
                            'migration-required: ' + author_handoff_migration_guidance())
        self.run_transition(('H',), 'I', 'migrate_workflow_document_revision_schema.py', org_args, 1,
                            'migration-required: ' + revision_migration_guidance())
        self.run_transition(('I',), 'J', 'migrate_workflow_coding_schema.py', args, 3,
                            'migration-needed: ' + coding_migration_guidance())

    def launch_process(self, pid: int, kind: str, rows: dict) -> dict:
        """Bind one checked Linux observation to executable/source/home custody."""
        row = rows.get(pid)
        if row is None or row.start_identity is None or row.ppid is None:
            raise Hold(f'{kind} process identity/ancestry unavailable; no import/restart')
        proc = Path('/proc') / str(pid)
        def stat_fields() -> tuple[str, str]:
            raw = (proc / 'stat').read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            if (len(fields) < 20 or fields[0] in ('Z', 'X')
                    or (int(fields[1]), fields[19]) != (row.ppid, row.start_identity)):
                raise Hold(f'{kind} process moved/reused/dead; original lifecycle owner must reconcile')
            return fields[1], fields[19]
        before = stat_fields()
        if proc.stat().st_uid != os.getuid():
            raise Hold(f'{kind} process custody differs from original human owner')
        def bounded(name: str) -> bytes:
            with (proc / name).open('rb') as stream:
                raw = stream.read(_LIMIT + 1)
            if len(raw) > _LIMIT:
                raise Hold(f'{kind} process observation oversized')
            return raw
        command = bounded('cmdline').split(b'\0')
        if command[-1:] != [b'']:
            raise Hold(f'{kind} command identity unobservable')
        command = command[:-1]
        expected = [b'-m', b'runtime.daemon', b'--reader-guarded-start=online-team-move@3']
        tail = [b'run', b'python', *expected] if kind == 'wrapper' else expected
        if len(command) != len(tail) + 1 or command[1:] != tail:
            raise Hold(f'{kind} is not the original lifecycle command')
        environment = bounded('environ').split(b'\0')
        env = dict(value.split(b'=', 1) for value in environment if b'=' in value)
        if (any(key.encode() in env for key in _MARKERS)
                or env.get(b'HOME') != str(Path.home()).encode()
                or (env.get(b'HAPPYRANCH_DAEMON_HOME') or str(Path.home() / '.happyranch').encode()) != str(self.home).encode()
                or any(key.encode() in env for key in ('PYTHONPATH', 'PYTHONHOME', 'UV_PROJECT_ENVIRONMENT', 'UV_PYTHON', 'UV_WORKING_DIRECTORY', 'UV_PROJECT'))):
            raise Hold(f'{kind} home/environment attribution differs or is unavailable')
        executable = os.readlink(proc / 'exe')
        cwd = os.readlink(proc / 'cwd')
        pin = self.tools['uv'] if kind == 'wrapper' else self.python_pin
        actual = os.stat(proc / 'exe')
        actual_identity = (actual.st_dev, actual.st_ino, actual.st_mode, actual.st_uid, actual.st_nlink)
        if (cwd != str(self.source) or executable != pin['target']
                or actual_identity != tuple(pin['identity']) or self.hash_file(proc / 'exe') != pin['sha256']):
            raise Hold(f'{kind} executable/source/custody mismatch')
        if (before != stat_fields() or os.readlink(proc / 'exe') != executable
                or os.readlink(proc / 'cwd') != cwd or bounded('cmdline').split(b'\0')[:-1] != command
                or bounded('environ').split(b'\0') != environment):
            raise Hold(f'{kind} process moved during attribution')
        # No raw environment/command payload is saved. Only closed identities.
        return {'pid': pid, 'start_identity': row.start_identity, 'ppid': row.ppid,
                'executable': executable, 'executable_identity': actual_identity,
                'cwd': cwd, 'home': str(self.home), 'uid': os.getuid()}

    def started_processes(self, wrapper_pid: int, port: int) -> dict:
        file_pins = {name: identity(self.home / name) for name in ('daemon.pid', 'daemon.port')}
        if any(pin[3] != os.getuid() for pin in file_pins.values()):
            raise Hold('original daemon pid/port custody mismatch; import held')
        pid_raw = read_file(self.home / 'daemon.pid', 64)
        port_raw = read_file(self.home / 'daemon.port', 64)
        if (re.fullmatch(rb'[1-9][0-9]{0,9}\n?', pid_raw) is None
                or port_raw.strip() != str(port).encode() or not 1 <= port <= 65535):
            raise Hold('original daemon pid/port file mismatch; import held')
        pid = int(pid_raw)
        rows = {r.pid: r for r in checked_processes()}
        wrapper = self.launch_process(wrapper_pid, 'wrapper', rows)
        daemon = self.launch_process(pid, 'daemon', rows)
        for process in (wrapper, daemon):
            key = (process['pid'], process['start_identity'])
            if (key in self.before_start or (self.original_process is not None and key == self.original_process)
                    or int(process['start_identity']) < self.start_floor):
                raise Hold('start reported an existing/reused/unattributed original process; import held')
        if (pid == wrapper_pid or daemon['ppid'] != wrapper_pid
                or int(daemon['start_identity']) < int(wrapper['start_identity'])):
            raise Hold('original uv wrapper/Python child relationship unavailable; import held')
        later = {r.pid: r for r in checked_processes()}
        if (self.launch_process(wrapper_pid, 'wrapper', later) != wrapper
                or self.launch_process(pid, 'daemon', later) != daemon
                or read_file(self.home / 'daemon.pid', 64) != pid_raw
                or read_file(self.home / 'daemon.port', 64) != port_raw
                or {name: identity(self.home / name) for name in file_pins} != file_pins):
            raise Hold('original start identities or pid/port files moved; import held')
        return {'wrapper': wrapper, 'daemon': daemon, 'port': port, 'files': file_pins,
                'start_floor': self.start_floor, 'original_pair_absent_before_start': True}

    def revalidate_start(self) -> None:
        if self.started_processes(self.started['wrapper']['pid'], self.started['port']) != self.started:
            raise Hold('original started pair changed; original lifecycle owner must reconcile; no import/restart')

    def serving(self):
        """Use existing observations; missing compatibility coverage holds import."""
        from cli.client.client import OpcClient
        from runtime.infrastructure.memory_collection import _identity_schema
        self.diagnostic.enter('serving-source-ui')
        self.revalidate()
        if self.ui_inventory() != self.ui:
            raise Hold('prepared served artifact moved')
        with OpcClient.from_env() as client:
            if hasattr(self, 'started') and client.base_url != f'http://127.0.0.1:{self.started["port"]}':
                raise Hold('serving client port differs from authenticated daemon; import held')
            health = client.get('/api/v1/health')
            if health.status_code != 200 or health.json().get('active_runtime') != str(self.runtime):
                raise Hold('serving runtime does not match selected registry')
            for org in self.frozen['orgs']:
                response = client.get(f'/api/v1/orgs/{quote(org["org"], safe="")}/audit', params={'action': 'memory_collection_seal', 'limit': 1})
                if response.status_code != 200:
                    raise Hold(f'{org["org"]}: loaded/broken org observation unavailable; original serving owner must reconcile')
                body = response.json()
                view = body.get('memory_collection_observation') if isinstance(body, dict) else None
                if not isinstance(view, dict):
                    raise Hold('serving response/view container unavailable', category='serving_observation_unavailable')
                loaded = view.get('installed_identity')
                if view.get('observation_error') is not None or not view.get('boot_id') or not loaded:
                    raise Hold(f'{org["org"]}: boot/loaded observation unavailable; original serving observer owner must reconcile')
                _identity_schema(loaded)  # closed rows before any set/dict projection
                if view['boot_id'] != self.boots.get(org['org']):
                    raise Hold('serving boot moved since live-scope bookend')
                # Require actual original workflow owners, not a disk HEAD or
                # healthy status inferred to cover unobserved loaded code.
                if (loaded.get('source_root') != str(self.source) or loaded.get('runtime_root') != org['runtime']
                        or loaded.get('org_root') != str(Path(org['path']).parent)
                        or loaded.get('python', {}).get('executable') != str(self.python.resolve(strict=True))):
                    raise Hold(f'{org["org"]}: serving source/runtime/org/interpreter identity mismatch')
                rows = loaded.get('loaded_code', [])
                observed = {(row.get('module'), row.get('qualname')) for row in rows}
                required = {('runtime.infrastructure.workflow_schema', name) for name in
                            ('validate_workflow_schema', '_read_workflow_schema', '_read_installed', '_validate_source_data', '_validate_release_database')}
                required |= {('runtime.infrastructure.workflow_coding_schema', name) for name in
                             ('validate_coding_data', '_read_coding_data', '_validate_coding_data')}
                required |= {('runtime.workflows.coding_release_import', 'CodingReleaseImportStore.' + name) for name in
                             ('get', 'import_release', '_current', '_receipt')}
                if not required <= observed:
                    raise Hold(f'{org["org"]}: loaded workflow compatibility owners unobserved: {sorted(required - observed)}; '
                               'existing selected memory observation is insufficient; original source/serving observer owner must resolve before import')
                files = {row['path']: row['sha256'] for row in loaded.get('files', [])}
                for module, name in required:
                    origin = str(self.source / (module.replace('.', '/') + '.py'))
                    row = next(row for row in rows if (row.get('module'), row.get('qualname')) == (module, name))
                    if (row.get('origin') != origin or not row.get('loaded_sha256')
                            or row.get('loaded_sha256') != row.get('source_code_sha256')
                            or files.get(origin) != self.hash_file(Path(origin))):
                        raise Hold(f'{org["org"]}: loaded workflow owner/source bytes mismatch: {module}.{name}')
            for name, expected in self.ui.items():
                response = client.get('/' + name)
                if response.status_code != 200 or digest(response.content) != expected:
                    raise Hold(f'served UI bytes mismatch: {name}')
            # Rendering is an actual human observation, separate from index byte
            # equality. Do not accept a checkbox to override missing code facts.
            port = read_file(self.home / 'daemon.port', 64).decode().strip()
            self.confirm(f'Open http://127.0.0.1:{port}/orgs/{self.args.org}/settings/coding in your browser. Confirm the actual Coding page renders, not a placeholder/error. Loaded owner and asset checks above remain required.')

        self.revalidate_start()
        self.diagnostic.effect('serving', 'accepted-selected-source-assets-human-render')
        self.diagnostic.accepted('serving')

    def import_release(self):
        from runtime.workflows._coding_release_history import parse_import_command
        from runtime.workflows.coding_release_import import ImportReceipt
        self.diagnostic.enter('import-original-readback')
        self.revalidate()
        show = [*self.cli, 'workflows', 'coding', 'release-import-show', '--org', self.args.org, '--json']
        code, out, err = self.command(show, accept=(0, 1), private=True)
        candidates = {self.import_file, self.home / f'coding-{self.args.org}-v1-import.json'}
        existing = [p for p in candidates if p.exists() or p.is_symlink()]
        if code == 0 and not err:
            receipt = ImportReceipt.model_validate_json(out)
            if receipt.replayed or receipt.current.state != 'admitted':
                raise Hold('saved import receipt is not currently admitted; original authority owner must reconcile')
            for path in existing:
                raw = read_file(path, 1024)
                command = parse_import_command(raw)
                if command['operation_key'] != receipt.operation_key or digest(raw) != receipt.request_digest:
                    raise Hold(f'original import file/receipt mismatch: {path}; preserve all originals; readback only')
            print('7. Existing admitted release import verified; POST skipped')
            self.diagnostic.effect('import-readback', 'accepted-existing-POST-skipped')
            self.diagnostic.accepted('import-existing-readback')
            return
        if (code, out, err) != (1, '', 'error (404): coding_release_import_not_found\n'):
            raise Hold('import readback failed; only exact coding_release_import_not_found is absence; no POST')
        effect = self.import_file.with_name(self.import_file.name + '.effect.json')
        literal(effect, missing=True)
        if existing or any(p.with_name(p.name + '.effect.json').exists() for p in candidates):
            raise Hold(f'original/uncertain import operation retained at {self.import_file}; use {show}; original import owner must reconcile; no POST/replay/new key')
        self.confirm('No import was found. Confirm you know of NO prior or uncertain import operation/file/send for this org. If unsure, decline.')
        self.revalidate()
        raw = canonical({'format': 'workflow-coding-release-import-command@1',
                         'operation_key': 'coding-setup-' + secrets.token_hex(16), 'expected_current_version': 0})
        command = parse_import_command(raw)
        self.diagnostic.enter('import-original-file')
        self.diagnostic.effect('import-file', 'exclusive-write-attempted')
        exclusive(self.import_file, raw)
        self.diagnostic.references['import_file'] = str(self.import_file)
        self.diagnostic.effect('import-file', 'accepted-durable-original-bytes')
        self.diagnostic.accepted('import-original-file')
        self.diagnostic.enter('import-original-intent')
        self.diagnostic.effect('import-intent', 'exclusive-write-attempted')
        exclusive(effect, canonical({'operation_file': str(self.import_file), 'operation_key': command['operation_key'],
                                     'request_digest': digest(raw), 'send_intent': True,
                                     'recovery': 'preserve original; release-import-show only; no automatic POST/replay/new key'}))
        self.diagnostic.references['import_intent'] = str(effect)
        self.diagnostic.effect('import-intent', 'accepted-durable-intent-not-send-proof')
        self.diagnostic.accepted('import-original-intent')
        self.diagnostic.enter('import-presend-revalidation')
        self.revalidate()
        if read_file(self.import_file, 1024) != raw:
            raise Hold('original import bytes changed before send; preserve original owner, no POST')
        # Exactly one invocation; every exception leaves original file/intent.
        self.diagnostic.enter('import-original-cli-send')
        code, out, err = self.command([*self.cli, 'workflows', 'coding', 'release-import', '--org', self.args.org,
                                      '--from-file', str(self.import_file), '--json'], private=True, effect='import-send')
        self.diagnostic.enter('import-receipt-validation')
        receipt = ImportReceipt.model_validate_json(out)
        if err or receipt.replayed or receipt.operation_key != command['operation_key'] or receipt.request_digest != digest(raw):
            raise Hold('import receipt mismatch; preserve original file/intent; original owner readback only')
        self.diagnostic.effect('import-receipt', 'exclusive-write-attempted')
        exclusive(self.receipts / 'import-post.json', canonical(receipt.model_dump()))
        self.diagnostic.references['import_post'] = str(self.receipts / 'import-post.json')
        self.diagnostic.effect('import-receipt', 'accepted-durable-receipt-readback-pending')
        self.diagnostic.accepted('import-receipt')
        self.diagnostic.enter('import-post-readback')
        self.diagnostic.effect('import-readback', 'attempted')
        code, out, err = self.command(show, private=True)
        readback = ImportReceipt.model_validate_json(out)
        if err or readback != receipt or readback.current.state != 'admitted':
            raise Hold('import readback mismatch/authority unavailable; preserve original owner; never resend')
        exclusive(self.receipts / 'import-readback.json', canonical(readback.model_dump()))
        self.diagnostic.references['import_readback'] = str(self.receipts / 'import-readback.json')
        self.diagnostic.effect('import-send', 'accepted-original-receipt-and-readback')
        self.diagnostic.effect('import-readback', 'accepted-durable-readback')
        self.diagnostic.accepted('import')

    def run(self):
        self.diagnostic.enter('initial-maintenance-revalidation')
        self.revalidate()
        print(f'1. Source {self.source} HEAD={self.source_pin["head"]}; runtime {self.runtime}; org {self.args.org}; layout {self.last_layout}')
        print(f'   Shared scope: {self.frozen["registry"]["registered"]}; UI {self.dist} ({self.args.ui_profile}); backup {self.backup}')
        self.confirm('Renew the already-established complete human/cooperative window: all affected registered runtimes, normal orgs, shared-daemon stores, source/environment users and other hosts remain excluded from work/publication/profile/move/recovery/restart through controlled restart, import checks and return. Stop if any actor/host is unknown. Repeated observations and SQLite reservations do not create a cross-host lock or hostile same-user/hard real-time guarantee.')
        print('2. Stopping original daemon once' if self.entry_running else '2. Positive already-stopped entry; stop not attempted')
        self.stop()
        self.backup_inventory()
        self.prepare()
        self.migrate()
        print('6. Starting selected compatible source once')
        self.diagnostic.enter('start-preflight')
        self.revalidate(offline=True)
        self.require_backup()
        self.before_start = {(r.pid, r.start_identity) for r in checked_processes()}
        self.start_floor = int(time.clock_gettime(time.CLOCK_BOOTTIME) * os.sysconf('SC_CLK_TCK'))
        self.diagnostic.enter('start-command')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'start'], private=True, effect='start')
        started = re.fullmatch(r'daemon started \(pid ([1-9][0-9]*), port ([1-9][0-9]*)\)\n', out)
        if started is None or err:
            raise Hold('original start returned an unexpected result; preserve original host owner, no automatic restart/import')
        wrapper_pid, port = (int(x) for x in started.groups())
        self.diagnostic.enter('start-pair-observation')
        self.diagnostic.effect('start-pair', 'observation-attempted')
        self.started = self.started_processes(wrapper_pid, port)
        self.offline = False
        exclusive(self.receipts / 'started-processes.json', canonical(self.started))
        self.diagnostic.references['started_pair'] = str(self.receipts / 'started-processes.json')
        self.diagnostic.effect('start-pair', 'accepted-pair-receipt-status-pending')
        self.boots = None  # Only this accepted original start permits a new boot.
        self.diagnostic.enter('start-status')
        code, out, err = self.command([self.tools['bash']['path'], 'scripts/daemon.sh', 'status'], private=True)
        self.revalidate_start()
        pid = self.started['daemon']['pid']
        if out != f'running (pid {pid}, port {port})\n' or err:
            raise Hold('original new status mismatches actual daemon PID/port; import held')
        self.diagnostic.effect('start', 'accepted-original-pair-and-status-serving-pending')
        self.diagnostic.accepted('start-pair-status')
        self.serving()
        print('7. Checking original human release import')
        self.import_release()
        self.revalidate()
        self.require_backup()
        self.diagnostic.enter('settings-disabled-handoff')
        port = read_file(self.home / 'daemon.port', 64).decode().strip()
        print(f'8. http://127.0.0.1:{port}/orgs/{self.args.org}/settings/coding')
        print('Adopt current metadata; select repositories and sha1/sha256 formats; choose four distinct eligible org agents across teams and an eligible final manager. Leave Admission Disabled. Save and reopen; uncertain saves use Check original save.')
        print(f'Backup: {self.backup}; original import: {self.import_file}')
        print('Host preparation and release import complete; choose agents in Settings → Coding. Coding remains disabled; workflow and operational acceptance are open.')


def identity_or_link(path):
    s = path.lstat()
    return (s.st_dev, s.st_ino, s.st_mode, s.st_uid, s.st_nlink)


def absolute_path(value):
    path = Path(value)
    if not path.is_absolute() or str(path) != value or '..' in path.parts:
        raise argparse.ArgumentTypeError('use an absolute literal path without aliases/dot segments')
    return path


def main(argv=None):
    diagnostic = DiagnosticContext()
    coordinator = None
    try:
        require_human_host()
        diagnostic.accepted('host-entry')
        diagnostic.enter('arguments')
        parser = SetupParser(description=__doc__, allow_abbrev=False)
        parser.add_argument('--org', required=True)
        parser.add_argument('--runtime-root', type=absolute_path)
        parser.add_argument('--backup-dir', type=absolute_path)
        parser.add_argument('--ui-profile', choices=('local', 'production'), default='local')
        parser.add_argument('--import-file', type=absolute_path)
        args = parser.parse_args(argv)
        if re.fullmatch(r'[a-z0-9-]{1,40}', args.org) is None:
            raise ArgumentRefusal()
        # Use only the existing installed source environment; never uv run here
        # (it may sync). Missing preflight dependencies give an owner hold.
        diagnostic.accepted('arguments')
        diagnostic.enter('cooperative-window-before-effects')
        if input('Before invoking setup (and any preceding host update), the human operator must control ALL affected runtime/org/shared-daemon stores, source/environment users and cooperative other hosts. Maintain no new work, publications, profiles, moves, recovery or competing restart through backup, preparation, migration, controlled restart, import checks and return. Unknown actors/hosts or unavailable current observations HOLD; a confirmation or PID absence alone is insufficient. Type CONFIRM only with this complete existing window: ') != 'CONFIRM':
            raise HostRefusal('complete cooperative maintenance window not established')
        diagnostic.accepted('cooperative-window-confirmed; observations-still-required')
        diagnostic.enter('selected-environment')
        selected = _SOURCE / '.venv' / 'bin' / 'python'
        if Path(sys.prefix) != _SOURCE / '.venv':
            if not selected.exists():
                raise HostRefusal('selected source .venv unavailable; original environment owner must supply preflight dependencies before setup')
            for directory in (_SOURCE / '.venv', _SOURCE / '.venv/bin'):
                if directory.is_symlink() or directory.stat().st_uid != os.getuid():
                    raise HostRefusal('selected environment has aliased/foreign custody; original environment owner must reconcile')
            diagnostic.enter('selected-environment-exec')
            os.execv(str(selected), [str(selected), str(Path(__file__).absolute()), *(sys.argv[1:] if argv is None else argv)])
        diagnostic.enter('preflight-helper-import')
        diagnostic.effect('initialization', 'helper-import-attempted-caches-possible')
        sys.path.insert(0, str(_SOURCE))
        global Hold, literal, identity, read_file, registry, observe_maintenance, read_database, checked_processes
        from scripts._coding_setup_preflight import (Hold, literal, identity, read_file, registry,
                                                     observe_maintenance, read_database, checked_processes)
        print('1. Inspecting installation, source and original maintenance owners')
        coordinator = Coordinator(args, diagnostic)
        diagnostic.enter('initial-revalidation')
        coordinator.run()
        return 0
    except SystemExit as exc:
        if diagnostic.stage == 'arguments' and exc.code == 0:
            return 0  # argparse help, after the unchanged host-entry guard
        if coordinator is not None:
            coordinator.accepted_backup = None
        diagnostic.report(exc)
        return 2 if diagnostic.stage == 'arguments' else 1
    except (Exception, KeyboardInterrupt) as exc:
        if coordinator is not None:
            coordinator.accepted_backup = None
        diagnostic.report(exc)
        return 2 if isinstance(exc, ArgumentRefusal) else 1


if __name__ == '__main__':
    raise SystemExit(main())
