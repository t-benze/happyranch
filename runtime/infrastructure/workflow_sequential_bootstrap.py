"""Original-owned automatic sequential storage setup.

The external attempt is a latch, never replay/restore authority. Generic
Database construction and the historical offline installer do not create it.
The coordinator keeps its original EX fence and descriptors until all owned
connections/workers close positively; this journal alone proves no such closure.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from runtime.infrastructure.org_access_fence import OrgAccessFence


class BootstrapEvidenceError(ValueError):
    """Closed cause; preserve original errors locally, never publish their text."""


_PHASES = ('intent', 'backup_verified', 'pre_commit', 'committed', 'verified', 'registered')
_STATES = {
    'intent': ('unknown', 'install_incomplete'),
    'backup_verified': ('unknown', 'install_incomplete'),
    'pre_commit': ('unknown', 'commit_uncertain'),
    'committed': ('installed_unverified', 'installation_unverified'),
    'verified': ('installed_unverified', 'installation_unverified'),
    'registered': ('installed_verified', 'registered'),
    'not_installed': ('not_installed', 'not_installed'),
}


def _close_descriptor(record: dict) -> None:
    record['attempted'] = True
    os.close(record['fd'])
    record['closed'] = True


def _directory(path: Path, custody: list[dict]) -> dict:
    """Literal absolute traversal, including all existing ancestors."""
    if not path.is_absolute() or str(path) != os.path.normpath(path) or '..' in path.parts:
        raise BootstrapEvidenceError('workflow_setup_path_invalid')
    record = dict(fd=os.open('/', os.O_RDONLY | os.O_DIRECTORY), attempted=False, closed=False)
    custody.append(record)
    for name in path.parts[1:]:
        child = dict(fd=os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                               dir_fd=record['fd']), attempted=False, closed=False)
        # Own the new descriptor BEFORE the preceding fallible close. On any
        # error the caller still owns both originals and attempts no blind retry.
        custody.append(child)
        _close_descriptor(record)
        record = child
    return record


def _private_directory(fd: int) -> None:
    info = os.fstat(fd)
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise BootstrapEvidenceError('workflow_setup_namespace_unsafe')


def _names(fd: int, limit: int, deadline: float) -> set[str]:
    found = set()
    with os.scandir(fd) as entries:
        for entry in entries:
            if len(found) >= limit or time.monotonic() >= deadline:
                raise BootstrapEvidenceError('workflow_setup_namespace_exhausted')
            found.add(entry.name)
    if time.monotonic() >= deadline:
        raise BootstrapEvidenceError('workflow_setup_deadline')
    return found


class BootstrapAttempt:
    """One non-replayable phase publisher under the original EX description.

    Construction is effect-free so its actual owner can retain this object
    before begin(), including partially constructed descriptor custody. begin()
    requires positive qualification by the caller; EX alone is not that proof.
    No source DB, backup bytes or native ownership is inferred or mutated here.
    """

    def __init__(self, *, home: Path, runtime_root: Path, slug: str,
                 fence: OrgAccessFence, source_identity: tuple,
                 source_inventory_digest: str, deadline: float) -> None:
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 0 < deadline - time.monotonic() <= 120
                or re.fullmatch('[a-z0-9-]{1,40}', slug) is None
                or type(source_inventory_digest) is not str
                or re.fullmatch('[0-9a-f]{64}', source_inventory_digest) is None
                or len(source_identity) != 4
                or source_identity[0] != str(runtime_root / 'orgs' / slug / 'happyranch.db')
                or any(type(value) is not int or value < 0 for value in source_identity[1:])
                or fence.root != runtime_root / 'orgs' / slug or not fence.exclusive):
            raise BootstrapEvidenceError('workflow_setup_owner_invalid')
        fence.assert_held()
        self.home, self.runtime_root, self.slug = Path(home), Path(runtime_root), slug
        self.fence, self.deadline = fence, deadline
        self.runtime_key = hashlib.sha256(str(runtime_root).encode('utf8')).hexdigest()
        self.source_identity, self.source_inventory_digest = source_identity, source_inventory_digest
        self.attempt_id = uuid.uuid4().hex
        self.attempt_time = datetime.now(timezone.utc).isoformat()
        self.namespace = self.home / 'backups/workflow-sequential' / self.runtime_key / slug
        self.root = self.namespace / self.attempt_id
        self.backup_path = self.root / 'before.db'
        self._descriptors: list[dict] = []
        self._entries: set[str] = set()
        self._parent: int | None = None
        self._sequence = 0
        self._previous: str | None = None
        self._phase: str | None = None
        self._backup_sha256: str | None = None
        self.failed = False
        self.closed = False

    def _check(self, *, reserve: bool = False) -> None:
        if self.failed or self.closed:
            raise BootstrapEvidenceError('workflow_setup_attempt_latched')
        self.fence.assert_held()
        if time.monotonic() >= self.deadline - (20 if reserve else 0):
            raise BootstrapEvidenceError('workflow_setup_commit_reserve' if reserve else 'workflow_setup_deadline')
        if self._parent is not None:
            # Original descriptors stay pinned; replacement/aliasing the path
            # cannot redirect a later publication into a different namespace.
            observed = _directory(self.root, self._descriptors)
            try:
                current, original = os.fstat(observed['fd']), os.fstat(self._parent)
                if (current.st_dev, current.st_ino) != (original.st_dev, original.st_ino):
                    raise BootstrapEvidenceError('workflow_setup_namespace_changed')
            finally:
                _close_descriptor(observed)

    def _own(self, fd: int) -> int:
        self._descriptors.append({'fd': fd, 'attempted': False, 'closed': False})
        return fd

    def begin(self) -> None:
        """Reserve an exclusive attempt and fsync intent before setup effects."""
        try:
            self._check(reserve=True)
            if self._descriptors or self._phase is not None:
                raise BootstrapEvidenceError('workflow_setup_attempt_latched')
            parent = _directory(self.home, self._descriptors)['fd']
            for name in ('backups', 'workflow-sequential', self.runtime_key, self.slug):
                self._check(reserve=True)
                try:
                    os.mkdir(name, 0o700, dir_fd=parent)
                except FileExistsError:
                    pass
                else:
                    os.fsync(parent)
                parent = self._own(os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
                _private_directory(parent)
            # Complete bounded discovery, never a selected prefix. No observed
            # old/failed/unknown attempt is automatic retry authority.
            if _names(parent, 64, min(self.deadline, time.monotonic()+1)):
                raise BootstrapEvidenceError('workflow_setup_attempt_latched')
            os.mkdir(self.attempt_id, 0o700, dir_fd=parent)
            os.fsync(parent)
            self._parent = self._own(os.open(self.attempt_id,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent))
            _private_directory(self._parent)
            self.publish('intent')
        except BaseException:
            self.failed = True
            raise

    def publish(self, phase: str, *, backup_sha256: str | None = None) -> None:
        """Append one bounded exact record; a failed append latches this owner.

        The caller proves backup/preservation/COMMIT/closure and final audit
        before selecting their phase. A pre_commit record alone remains unknown.
        A terminal not_installed requires a positively pre-COMMIT original
        rollback/close/inventory proof; it is never a catch-all error handler.
        """
        try:
            self._check(reserve=phase in ('intent', 'backup_verified', 'pre_commit'))
            if self._parent is None or self._sequence >= 16:
                raise BootstrapEvidenceError('workflow_setup_phase_invalid')
            terminal = phase == 'not_installed' and self._phase in ('intent', 'backup_verified', 'pre_commit')
            if (not terminal and (self._sequence >= len(_PHASES) or phase != _PHASES[self._sequence])
                    or self._phase in ('not_installed', 'registered')):
                raise BootstrapEvidenceError('workflow_setup_phase_invalid')
            if backup_sha256 is not None:
                if (type(backup_sha256) is not str or re.fullmatch('[0-9a-f]{64}', backup_sha256) is None
                        or self._backup_sha256 not in (None, backup_sha256)):
                    raise BootstrapEvidenceError('workflow_setup_backup_changed')
            digest = self._backup_sha256 if backup_sha256 is None else backup_sha256
            if phase not in ('intent', 'not_installed') and digest is None:
                raise BootstrapEvidenceError('workflow_setup_backup_unproven')
            expected = self._entries.copy()
            if phase != 'intent' and (phase != 'not_installed' or digest is not None):
                # Backup construction belongs between intent and backup_verified.
                # No SQLite sidecars, pending file or unexplained entry is accepted.
                expected.add('before.db')
            elif phase == 'not_installed':
                # A positively unchanged refusal may precede backup creation.
                # A partial backup is retained if the caller positively closed it.
                if 'before.db' in _names(self._parent, 17, min(self.deadline, time.monotonic()+1)):
                    expected.add('before.db')
            if _names(self._parent, 17, min(self.deadline, time.monotonic()+1)) != expected:
                raise BootstrapEvidenceError('workflow_setup_namespace_changed')
            if 'before.db' in expected:
                info = os.stat('before.db', dir_fd=self._parent, follow_symlinks=False)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600):
                    raise BootstrapEvidenceError('workflow_setup_backup_unsafe')
            state, reason = _STATES[phase]
            value = dict(format='workflow-setup-event@1', runtime_key=self.runtime_key,
                org_slug=self.slug, attempt_id=self.attempt_id, sequence=self._sequence,
                previous_digest=self._previous, phase=phase, state=state, reason=reason,
                attempt_time=self.attempt_time, backup_path=str(self.backup_path),
                db_identity=dict(path=self.source_identity[0], device=self.source_identity[1], inode=self.source_identity[2]),
                source_inventory_digest=self.source_inventory_digest, backup_sha256=digest, target_version=1)
            raw = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf8')
            if len(raw) > 65536:
                raise BootstrapEvidenceError('workflow_setup_record_exhausted')
            name = f'{self._sequence:02}.json'
            pending = name + '.pending'
            fd = self._own(os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                   0o600, dir_fd=self._parent))
            record = self._descriptors[-1]
            remaining = memoryview(raw)
            while remaining:
                self._check(reserve=phase == 'pre_commit')
                written = os.write(fd, remaining)
                if written <= 0:
                    raise BootstrapEvidenceError('workflow_setup_short_write')
                remaining = remaining[written:]
            os.fsync(fd)
            record['attempted'] = True
            os.close(fd)
            record['closed'] = True
            self._check(reserve=phase == 'pre_commit')
            # link() is a no-overwrite publication on both supported platforms;
            # an interrupted pending/multiply-linked inode makes readers refuse.
            os.link(pending, name, src_dir_fd=self._parent, dst_dir_fd=self._parent, follow_symlinks=False)
            os.unlink(pending, dir_fd=self._parent)
            os.fsync(self._parent)
            self._check(reserve=phase == 'pre_commit')
            self._entries = expected | {name}
            self._sequence += 1
            self._previous = hashlib.sha256(raw).hexdigest()
            self._phase, self._backup_sha256 = phase, digest
        except BaseException:
            self.failed = True
            raise

    def close(self) -> None:
        """One close attempt per original descriptor, retaining uncertainty."""
        errors = []
        for record in reversed(self._descriptors):
            if record['attempted']:
                if not record['closed']:
                    errors.append(record)
                continue
            record['attempted'] = True
            try:
                os.close(record['fd'])
            except OSError:
                errors.append(record)
            else:
                record['closed'] = True
        if errors:
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_descriptor_close_unknown')
        self.closed = True
