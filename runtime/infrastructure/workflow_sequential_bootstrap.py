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
import shutil
import socket
import sqlite3
import stat
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from runtime.config import Settings
from runtime.infrastructure.org_access_fence import OrgAccessFence, OrgAccessHeld
from runtime.infrastructure.workflow_integrity import IntegrityLedger, require_install_integrity
from runtime.infrastructure.workflow_schema import (
    SequentialInstallAllowance, _coding_org_identity, inspect_sequential_install,
    sequential_storage_inventory,
)


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
    try:
        os.close(record['fd'])
    except BaseException as exc:
        record['error'] = exc
        raise
    record['closed'] = True


def _directory(path: Path, custody: list[dict], *, deadline: float | None = None) -> dict:
    """Literal absolute traversal, including all existing ancestors."""
    if not path.is_absolute() or str(path) != os.path.normpath(path) or '..' in path.parts:
        raise BootstrapEvidenceError('workflow_setup_path_invalid')
    record = dict(fd=os.open('/', os.O_RDONLY | os.O_DIRECTORY), attempted=False, closed=False)
    custody.append(record)
    for name in path.parts[1:]:
        if deadline is not None and time.monotonic() >= deadline:
            raise BootstrapEvidenceError('workflow_setup_deadline')
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
                 source_inventory_digest: str, deadline: float,
                 control: BootstrapControl | None = None) -> None:
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
        self.control = control
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
        if self.control is not None:
            self.control.check()
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


def _file(path: Path) -> tuple:
    value = path.lstat()
    if (not stat.S_ISREG(value.st_mode) or value.st_uid != os.getuid()
            or value.st_nlink != 1):
        raise BootstrapEvidenceError('workflow_setup_file_unsafe')
    return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _companions(path: Path) -> tuple:
    result = []
    for suffix in ('', '-wal', '-shm', '-journal'):
        candidate = path.with_name(path.name + suffix)
        try:
            value = _file(candidate)
        except FileNotFoundError:
            if not suffix:
                raise
            value = None
        result.append(value)
    if result[3] is not None:
        # Recovery/old-owner residue is never checkpoint or unlink authority.
        raise BootstrapEvidenceError('workflow_setup_journal_unresolved')
    if (result[1] is None) != (result[2] is None):
        raise BootstrapEvidenceError('workflow_setup_sidecars_unresolved')
    return tuple(result)


class BootstrapSelection:
    """Bounded RO routing before construction, under the caller's original fence.

    'present' only routes to normal complete validation; partial reserved objects
    never authorize repair or installation. 'absent' grants no source/native
    qualification. The coordinator must close SH, acquire EX, and rediscover
    everything with a new owner before considering any absent-org effects.
    Neither result replaces ordinary parts 2-7 or the actual writable rehearsal.
    """

    def __init__(self, *, home: Path, fence: OrgAccessFence, deadline: float) -> None:
        if type(deadline) not in (int, float) or not math.isfinite(deadline):
            raise BootstrapEvidenceError('workflow_setup_owner_invalid')
        self.home, self.fence = Path(home), fence
        self.deadline = min(deadline, time.monotonic()+5)
        self.path = fence.root / 'happyranch.db'
        self.connections: list[dict] = []
        self.descriptors: list[dict] = []
        self.source_identity: tuple | None = None
        self.attempt: dict | None = None
        self.result: str | None = None
        self.started = self.failed = self.closed = False
        self.primary: BaseException | None = None
        self.close_error: BaseException | None = None


    def _check(self) -> None:
        self.fence.assert_held()
        if time.monotonic() >= self.deadline:
            raise BootstrapEvidenceError('workflow_setup_selection_deadline')

    @contextmanager
    def _evidence_directory(self, path: Path, deadline: float) -> Iterator[int]:
        """Keep evidence originals in this selection's shutdown custody."""
        record = None
        try:
            record = _directory(path, self.descriptors, deadline=deadline)
            yield record['fd']
        finally:
            # Latch a traversal close failure before any further cleanup can
            # fail. _directory already retained its child before that syscall.
            for original in self.descriptors:
                if original.get('error') is not None:
                    self.close_error = self.close_error or original['error']
            if record is not None:
                try:
                    _close_descriptor(record)
                except BaseException as exc:
                    self.close_error = self.close_error or exc
                    raise

    def _read_evidence(self, deadline: float) -> dict:
        """The single startup evidence path, always using this retained owner."""
        from runtime.daemon.workflow_setup_status import _attempt_record
        self._check()
        runtime_root = self.fence.root.parent.parent
        key = hashlib.sha256(str(runtime_root).encode('utf8')).hexdigest()
        return _attempt_record(self.home, runtime_root, key, self.fence.root.name,
            min(self.deadline, deadline), directory_reader=self._evidence_directory)

    def _prior(self) -> dict | None:
        self._check()
        evidence_deadline = min(self.deadline, time.monotonic()+1)
        home = _directory(self.home, self.descriptors)
        _close_descriptor(home)
        runtime_root = self.fence.root.parent.parent
        key = hashlib.sha256(str(runtime_root).encode('utf8')).hexdigest()
        namespace = self.home / 'backups/workflow-sequential' / key / self.fence.root.name
        try:
            parent = _directory(namespace, self.descriptors)
        except FileNotFoundError:
            return None
        try:
            _private_directory(parent['fd'])
            names = _names(parent['fd'], 64, evidence_deadline)
            if not names:
                return None
            value = self._read_evidence(evidence_deadline)
            if (value['state'], value['reason'], value['phase']) != (
                    'installed_verified', 'registered', 'registered'):
                raise BootstrapEvidenceError('workflow_setup_attempt_latched')
            return value
        except (OSError, ValueError, TimeoutError, TypeError, KeyError, RecursionError) as exc:
            raise BootstrapEvidenceError('workflow_setup_attempt_latched') from exc
        finally:
            _close_descriptor(parent)

    def read(self) -> str:
        """No constructor, full source/integrity/inventory, backup or installer.

        Retain every original handle before PRAGMA/identity/evidence work. A
        result escapes only after positive closure; unknown close never retries.
        Prior failures (even positively not_installed) forbid another attempt.
        """
        from runtime.infrastructure.workflow_schema_load import sequential_storage_present
        from runtime.workflows.sequential_history import _ReadBudget
        if self.started or self.failed or self.closed:
            raise BootstrapEvidenceError('workflow_setup_selection_latched')
        self.started = True
        try:
            self._check()
            self.attempt = self._prior()
            parent = _directory(self.path.parent, self.descriptors)
            _companions(self.path)
            before = _file(self.path)
            conn = sqlite3.connect(self.path.as_uri()+'?mode=ro', uri=True, timeout=0)
            self.connections.append(dict(connection=conn, attempted=False, closed=False))
            conn.execute('PRAGMA query_only=ON')
            conn.row_factory = sqlite3.Row
            self.source_identity = _coding_org_identity(conn, expected_org_slug=self.fence.root.name,
                                                        expected_path=str(self.path))
            conn.execute('BEGIN')
            budget = _ReadBudget(deadline=self.deadline)
            present = sequential_storage_present(conn, budget=budget)
            budget.check()
            self._check()
            if _file(self.path) != before or _coding_org_identity(conn,
                    expected_org_slug=self.fence.root.name, expected_path=str(self.path)) != self.source_identity:
                raise BootstrapEvidenceError('workflow_setup_source_changed')
            if self.attempt is not None:
                identity = self.attempt['db_identity']
                if not present or (identity['path'], identity['device'], identity['inode']) != self.source_identity[:3]:
                    raise BootstrapEvidenceError('workflow_setup_attempt_latched')
            _close_descriptor(parent)
            self.close()
            self._check()
            self.result = 'present' if present else 'absent'
            return self.result
        except BaseException as exc:
            self.failed, self.primary = True, exc
            try:
                self.close()
            except BaseException as close_error:
                self.close_error = self.close_error or close_error
            raise

    def close(self) -> None:
        errors = []
        for record in reversed(self.connections):
            if record['attempted']:
                if not record['closed']:
                    errors.append(record['error'])
                continue
            record['attempted'] = True
            try:
                record['connection'].close()
            except BaseException as exc:
                record['error'] = exc
                errors.append(exc)
            else:
                record['closed'] = True
        for record in reversed(self.descriptors):
            if record['attempted']:
                if not record['closed']:
                    errors.append(record['error'])
                continue
            try:
                _close_descriptor(record)
            except BaseException as exc:
                errors.append(exc)
        if errors:
            self.failed = True
            self.close_error = self.close_error or errors[0]
            raise BootstrapEvidenceError('workflow_setup_selection_close_unknown') from self.close_error
        self.closed = True


class BootstrapStartup:
    """One startup's original discovery clock and cumulative admission ledger.

    This owner allocates no install attempt and proves no native-host closure.
    The coordinator must retain each discovery before read(), and qualify absent
    storage under its returned ORIGINAL EX before any backup or source effects.
    Installed selection/ordinary attachment do not borrow the upgrade exception.
    """

    def __init__(self, *, home: Path, runtime_root: Path) -> None:
        self.home, self.runtime_root = Path(home), Path(runtime_root)
        self.started = time.monotonic()
        self.deadline = self.started + 180
        self.admission_seconds = 0.0
        self.discoveries: dict[Path, BootstrapDiscovery] = {}
        self.workers: list[BootstrapSupervisor] = []
        self.holds: dict[str, str] = {}
        self.hold_descriptors: list[dict] = []
        self.upgrade_seconds = 0.0
        self.prepared = False
        self.loop = self.thread = None

    def run(self, roots: tuple[tuple[str, Path], ...], settings: Settings) -> None:
        """Keep the original event loop alive when native spawn/reap is unknown.

        asyncio.run would cancel pending originals on return, losing their late
        child kill/reap callbacks. A retained owner cannot use that teardown.
        """
        import asyncio
        import threading
        if self.loop is not None:
            raise BootstrapEvidenceError('workflow_setup_startup_latched')
        self.loop = asyncio.new_event_loop()
        try:
            self.loop.run_until_complete(self.prepare(roots, settings))
        finally:
            if asyncio.all_tasks(self.loop):
                self.thread = threading.Thread(target=self.loop.run_forever,
                    name='workflow-bootstrap-original', daemon=True)
                self.thread.start()
            else:
                self.loop.close()

    def require_closed(self) -> None:
        if any(not worker.reaped or not worker.closed and not worker.discovery.fence._closed
               for worker in self.workers):
            raise BootstrapEvidenceError('workflow_setup_original_worker_in_flight')
        for discovery in self.discoveries.values():
            # Pre-worker failures still own their original selectors and fences.
            # Observe custody only: a failed native close is never retried here.
            if (any(not record['closed'] for selected in discovery.selections
                    for record in (*selected.connections, *selected.descriptors))
                    or any(not record['transferred'] and not record['fence']._closed
                           for record in discovery.fences)):
                raise BootstrapEvidenceError('workflow_setup_original_discovery_in_flight') from discovery.close_error
        for record in self.hold_descriptors:
            if not record['closed']:
                raise BootstrapEvidenceError('workflow_setup_original_descriptor_in_flight') from record.get('error')

    def _hold(self, slug: str, reason: str, state: str) -> None:
        """External pre-intent latch only; never obscure an existing attempt."""
        self.holds[slug] = reason
        parent = _directory(self.home, self.hold_descriptors)['fd']
        key = hashlib.sha256(str(self.runtime_root).encode('utf8')).hexdigest()
        for name in ('backups', 'workflow-sequential', key, slug):
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            else:
                os.fsync(parent)
            child = dict(fd=os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                   dir_fd=parent), attempted=False, closed=False)
            self.hold_descriptors.append(child)
            parent = child['fd']
            _private_directory(parent)
        if _names(parent, 64, time.monotonic()+1):
            return
        value = dict(format='workflow-setup-hold@1', runtime_key=key, org_slug=slug,
                     reason=reason, state=state, attempt_time=datetime.now(timezone.utc).isoformat())
        raw = json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf8')
        record = dict(fd=os.open('hold.pending', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                0o600, dir_fd=parent), attempted=False, closed=False)
        self.hold_descriptors.append(record)
        if os.write(record['fd'], raw) != len(raw):
            raise BootstrapEvidenceError('workflow_setup_short_write')
        os.fsync(record['fd'])
        _close_descriptor(record)
        os.link('hold.pending', 'hold.json', src_dir_fd=parent, dst_dir_fd=parent, follow_symlinks=False)
        os.unlink('hold.pending', dir_fd=parent)
        os.fsync(parent)

    async def prepare(self, roots: tuple[tuple[str, Path], ...], settings: Settings) -> None:
        """Startup-only, serial upgrades before ordinary writable attachment.

        A4:2 uses the normal one-daemon startup-migration trust, original A1 EX
        and the helper's full integrity/backup/history checks. No predecessor
        receipt or freshly empty host-owner map is used as authority.
        """
        if self.prepared:
            raise BootstrapEvidenceError('workflow_setup_startup_latched')
        self.prepared = True
        for slug, root in roots:
            discovery = None
            worker = None
            started = time.monotonic()
            try:
                discovery = self.discover(root)
                selected = discovery.read()
                if selected == 'held':
                    raise BootstrapEvidenceError(discovery.reason)
                if selected == 'present':
                    continue
                worker = BootstrapSupervisor(discovery, settings)

                async def qualify(identity: tuple, digest: str) -> bool:
                    discovery.fence.assert_held()
                    if (not discovery.fence.exclusive or discovery.failed or discovery.closed
                            or identity != discovery.selections[-1].source_identity):
                        raise BootstrapEvidenceError('writer_exclusion_unproven')
                    return True

                try:
                    await worker.run(qualify)
                finally:
                    self.upgrade_seconds += max(0.0, time.monotonic()-started)
                # The original child returned, positively closed and reaped;
                # final registered evidence must agree before releasing EX.
                # A fresh retained selection owns this read too. Reusing a
                # closed pre-worker selection would misstate new native custody.
                selected = BootstrapSelection(home=self.home, fence=discovery.fence,
                    deadline=min(discovery.deadline, time.monotonic()+1))
                discovery.selections.append(selected)
                record = selected._read_evidence(selected.deadline)
                if (record['phase'] != 'registered' or record['attempt_id'] != worker.attempt_id
                        or record['db_identity'] != dict(path=str(root/'happyranch.db'),
                            device=discovery.fence.database_identity[0], inode=discovery.fence.database_identity[1])):
                    raise BootstrapEvidenceError('workflow_setup_registration_unavailable')
                discovery.close()
            except Exception as exc:
                reason = (worker.result['reason'] if worker is not None and worker.result and worker.result['state'] != 'verified' else
                          str(exc) if isinstance(exc, BootstrapEvidenceError) and str(exc) in (
                              'writer_exclusion_unproven', 'startup_upgrade_budget_unavailable') else
                          'writer_exclusion_unproven' if isinstance(exc, OrgAccessHeld) else
                          'install_incomplete')
                if reason not in ('writer_exclusion_unproven', 'startup_upgrade_budget_unavailable',
                        'history_check_failed', 'integrity_check_failed', 'backup_verification_failed',
                        'registration_failed', 'default_load_budget_exceeded', 'commit_uncertain'):
                    reason = 'install_incomplete'
                self.holds[slug] = reason
                try:
                    if discovery is not None:
                        state = ('not_installed' if discovery.result in ('absent', 'held')
                                 and (worker is None or worker.closed and not worker.permit_sent) else 'unknown')
                        self._hold(slug, reason, state)
                    # Count exhaustion is visible in holds/broken_orgs, without
                    # acquiring DB, fence or publication descriptors past the cap.
                except Exception:
                    # Broken-org status remains visible even if external evidence
                    # cannot be published. Never infer a persisted latch here.
                    pass
                try:
                    # run retains its worker before any socket/spawn acquisition.
                    # A pre-admission refusal owns only this discovery, even
                    # when a different org's original worker remains pending.
                    if discovery is not None and (worker is None or worker not in self.workers
                            or worker.reaped and worker.closed):
                        discovery.close()
                except Exception:
                    pass  # Original unknown custody remains in this startup owner.
            finally:
                for record in reversed(self.hold_descriptors):
                    if not record['attempted']:
                        try:
                            _close_descriptor(record)
                        except OSError as exc:
                            record['error'] = exc
                            self.holds[slug] = 'install_incomplete'

    def take_shared(self, root: Path) -> OrgAccessFence:
        discovery = self.discoveries[root]
        if discovery.result == 'present' and not discovery.closed:
            return discovery.take_shared()
        # A successful upgrade released EX only after original closure. The
        # normal attachment is a fresh SH interval, without another installer.
        if root.name in self.holds or not discovery.closed:
            raise BootstrapEvidenceError('workflow_setup_discovery_transfer_refused')
        fence = self._acquire(discovery, exclusive=False, deadline=time.monotonic()+5)
        discovery.fences[-1]['transferred'] = True
        return fence

    def discover(self, root: Path) -> BootstrapDiscovery:
        root = Path(root)
        if (root.parent != self.runtime_root/'orgs'
                or re.fullmatch('[a-z0-9-]{1,40}', root.name) is None
                or not root.is_absolute() or root in self.discoveries):
            raise BootstrapEvidenceError('workflow_setup_discovery_owner_invalid')
        if len(self.discoveries) >= 1024:
            raise BootstrapEvidenceError('startup_upgrade_budget_unavailable')
        discovery = BootstrapDiscovery(self, root)
        self.discoveries[root] = discovery
        return discovery

    def _acquire(self, discovery: BootstrapDiscovery, *, exclusive: bool,
                 deadline: float) -> OrgAccessFence:
        started = time.monotonic()
        if started >= deadline:
            raise BootstrapEvidenceError('workflow_setup_selection_deadline')
        remaining = max(0.0, .5-self.admission_seconds)
        try:
            # Once the common wait is spent, the native API's no-deadline form
            # still makes ONE nonblocking acquisition; no new .5s is supplied.
            fence = OrgAccessFence.acquire(discovery.root, exclusive=exclusive,
                **({'deadline': min(deadline, started+remaining)} if remaining else {}))
            discovery.fences.append(dict(fence=fence, attempted=False, closed=False, transferred=False))
            fence.assert_held()
            if time.monotonic() >= deadline:
                raise BootstrapEvidenceError('workflow_setup_selection_deadline')
            return fence
        finally:
            self.admission_seconds += max(0.0, time.monotonic()-started)


class BootstrapDiscovery:
    """Owned SH selection, positive SH close and fresh EX rediscovery.

    A hold retains SH until the startup coordinator closes known custody;
    it never authorizes fallback writable loading. Unknown closure never
    releases or transfers any original fence.
    A terminal/failed attempt is a refusal, never a new discovery permission.
    """

    def __init__(self, startup: BootstrapStartup, root: Path) -> None:
        self.startup, self.root = startup, root
        self.started_at = time.monotonic()
        self.deadline = min(self.started_at+120, startup.deadline)
        self.selection_deadline = self.started_at+5
        self.fences: list[dict] = []
        self.selections: list[BootstrapSelection] = []
        self.fence: OrgAccessFence | None = None
        self.result: str | None = None
        self.reason: str | None = None
        self.started = self.failed = self.closed = False
        self.primary: BaseException | None = None
        self.close_error: BaseException | None = None
        self.exclusion_error: BaseException | None = None

    def _select(self, fence: OrgAccessFence, deadline: float) -> str:
        selected = BootstrapSelection(home=self.startup.home, fence=fence, deadline=deadline)
        self.selections.append(selected)
        return selected.read()

    def _close_fence(self, record: dict) -> None:
        if record['transferred']:
            return
        if record['attempted']:
            if not record['closed']:
                raise BootstrapEvidenceError('workflow_setup_discovery_close_unknown') from record['error']
            return
        record['attempted'] = True
        try:
            record['fence'].close()
        except BaseException as exc:
            record['error'] = exc
            self.close_error = self.close_error or exc
            record['fence'].retain_unknown()
            raise
        record['closed'] = True

    def read(self) -> str:
        if self.started or self.failed or self.closed:
            raise BootstrapEvidenceError('workflow_setup_discovery_latched')
        self.started = True
        try:
            self.fence = self.startup._acquire(self, exclusive=False, deadline=self.selection_deadline)
            if self._select(self.fence, self.selection_deadline) == 'present':
                self.result = 'present'
                return self.result
            now = time.monotonic()
            if self.startup.deadline-now < 60 or self.deadline-now <= 20:
                self.reason = 'startup_upgrade_budget_unavailable'
                self.result = 'held'
                return self.result
            # No lock conversion, original selector snapshot or old absence is
            # carried across this gap. The EX selection uses its own raw read.
            self._close_fence(self.fences[-1])
            try:
                self.fence = self.startup._acquire(self, exclusive=True,
                    deadline=min(self.deadline-20, time.monotonic()+5))
            except OrgAccessHeld as exc:
                if str(exc) != 'org_access_busy':
                    raise
                self.exclusion_error = exc
                end = min(self.deadline, time.monotonic()+5)
                self.fence = self.startup._acquire(self, exclusive=False, deadline=end)
                selected = self._select(self.fence, end)
                self.reason = 'writer_exclusion_unproven' if selected == 'absent' else None
                self.result = 'held' if selected == 'absent' else 'present'
                return self.result
            selected = self._select(self.fence, min(self.deadline-20, time.monotonic()+5))
            if selected == 'present':
                # Another supported owner may install in the SH->EX gap. It is
                # ordinary storage now: reattach under a freshly selected SH.
                self._close_fence(self.fences[-1])
                end = min(self.deadline, time.monotonic()+5)
                self.fence = self.startup._acquire(self, exclusive=False, deadline=end)
                if self._select(self.fence, end) != 'present':
                    raise BootstrapEvidenceError('workflow_setup_discovery_changed')
            self.result = selected
            return self.result
        except BaseException as exc:
            self.failed, self.primary = True, exc
            try:
                self.close()
            except BaseException as close_error:
                self.close_error = self.close_error or close_error
            raise

    def take_shared(self) -> OrgAccessFence:
        """Transfer once into the normal loader's original attachment custody."""
        if (self.failed or self.closed or self.result not in ('present', 'held')
                or self.fence is None or self.fence.exclusive
                or any(not selected.closed or selected.failed for selected in self.selections)):
            raise BootstrapEvidenceError('workflow_setup_discovery_transfer_refused')
        record = self.fences[-1]
        if record['transferred'] or record['attempted'] or record['fence'] is not self.fence:
            raise BootstrapEvidenceError('workflow_setup_discovery_transfer_refused')
        self.fence.assert_held()
        record['transferred'] = True
        self.closed = True
        return self.fence

    def close(self) -> None:
        if any(worker.discovery is self and worker.started and not worker.reaped
               for worker in self.startup.workers):
            self.failed = True
            self.close_error = self.close_error or BootstrapEvidenceError('workflow_setup_original_worker_in_flight')
            if self.fence is not None:
                self.fence.retain_unknown()
            raise self.close_error
        errors = []
        for selected in reversed(self.selections):
            try:
                selected.close()
            except BaseException as exc:
                self.close_error = self.close_error or selected.close_error or exc
                errors.append(exc)
        # Unclosed SQLite/descriptor ownership keeps its ORIGINAL fence.
        if not errors:
            for record in reversed(self.fences):
                try:
                    self._close_fence(record)
                except BaseException as exc:
                    errors.append(exc)
        if errors:
            self.failed = True
            self.close_error = self.close_error or errors[0]
            for record in self.fences:
                if not record['closed'] and not record['transferred']:
                    record['fence'].retain_unknown()
            raise BootstrapEvidenceError('workflow_setup_discovery_close_unknown') from self.close_error
        self.closed = True


class BootstrapSource:
    """Original read snapshot and independent backup, before source effects.

    EX supplies cooperative exclusion only. The coordinator must independently
    qualify EX and complete durable history BEFORE allocating an attempt (A4:2).
    Construction performs no opens so custody survives partial open()/backup().
    No method checkpoints, repairs, installs, restores, replays or releases EX.
    """

    def __init__(self, *, fence: OrgAccessFence, deadline: float,
                 control: BootstrapControl | None = None) -> None:
        if (not fence.exclusive or type(deadline) not in (int, float)
                or not math.isfinite(deadline) or not 20 < deadline-time.monotonic() <= 120):
            raise BootstrapEvidenceError('workflow_setup_owner_invalid')
        self.fence, self.deadline = fence, deadline
        self.control = control
        self.path = fence.root / 'happyranch.db'
        # Both stages and later writer/target full checks share THIS ledger.
        self.integrity = IntegrityLedger(deadline=deadline-20)
        self.allowance = SequentialInstallAllowance(deadline=deadline-20)
        self.connections: list[dict] = []
        self.descriptors: list[dict] = []
        self.source: sqlite3.Connection | None = None
        self.source_identity: tuple | None = None
        self.source_inventory: tuple | None = None
        self.inventory_digest: str | None = None
        self.layout = None
        self.image_bytes = 0
        self.page_size = 0
        self.observation: tuple | None = None
        self.backup_sha256: str | None = None
        self._inventory_steps = 0
        self._backup_started = False
        self._history_started = False
        self.failed = self.closed = False

    def _check(self) -> None:
        if self.failed or self.closed:
            raise BootstrapEvidenceError('workflow_setup_source_latched')
        if self.control is not None:
            self.control.check()
        self.fence.assert_held()
        self.allowance.check()

    def _connection(self, path: Path, *, readonly: bool) -> sqlite3.Connection:
        self._check()
        parent = _directory(path.parent, self.descriptors)
        before = _file(path)
        conn = sqlite3.connect(path.as_uri() + ('?mode=ro' if readonly else '?mode=rw'),
                               uri=True, timeout=0)
        # Retain before PRAGMA, native cursor work or any other fallible setup.
        self.connections.append(dict(connection=conn, attempted=False, closed=False))
        if path == self.path:
            # SQLite may otherwise checkpoint on close even after a refused
            # transaction. Only our explicit post-COMMIT checkpoint may move
            # retained WAL bytes into the source DB.
            conn.setconfig(sqlite3.SQLITE_DBCONFIG_NO_CKPT_ON_CLOSE, True)
        conn.row_factory = sqlite3.Row
        if readonly:
            conn.execute('PRAGMA query_only=ON')
        conn.execute('PRAGMA foreign_keys=ON')
        databases = [tuple(row) for row in conn.execute('PRAGMA database_list')]
        if (databases != [(0, 'main', str(path))] or _file(path) != before):
            raise BootstrapEvidenceError('workflow_setup_connection_identity_changed')
        _close_descriptor(parent)
        return conn

    def _close_connection(self, conn: sqlite3.Connection) -> None:
        record = next(value for value in self.connections if value['connection'] is conn)
        if record['attempted']:
            if not record['closed']:
                raise BootstrapEvidenceError('workflow_setup_connection_close_unknown')
            return
        record['attempted'] = True
        try:
            conn.close()
        except BaseException as exc:
            record['error'] = exc
            raise
        record['closed'] = True

    @contextmanager
    def _inventory_budget(self, conn: sqlite3.Connection):
        """Bound inventory SQL as well as its original Python row/byte limits."""
        def progress():
            self._inventory_steps += 1000
            return int(self._inventory_steps > 640000000 or time.monotonic() >= self.allowance.deadline)
        self._inventory_steps += 1000
        if self._inventory_steps > 640000000:
            raise BootstrapEvidenceError('workflow_setup_inventory_exhausted')
        try:
            conn.set_progress_handler(progress, 1000)
            yield
        finally:
            conn.set_progress_handler(None, 0)
        self._check()

    def _inventory(self, conn: sqlite3.Connection) -> tuple:
        with self._inventory_budget(conn):
            return sequential_storage_inventory(conn, allowance=self.allowance)

    def _unchanged(self) -> None:
        self._check()
        if self.observation != _companions(self.path):
            raise BootstrapEvidenceError('workflow_setup_source_changed')
        if (self.source is None or _coding_org_identity(self.source,
                expected_org_slug=self.fence.root.name, expected_path=str(self.path)) != self.source_identity):
            raise BootstrapEvidenceError('workflow_setup_source_changed')

    def open(self) -> None:
        """Retain one coherent WAL-aware read snapshot; do no source writes."""
        try:
            self._check()
            if self.connections or self.descriptors:
                raise BootstrapEvidenceError('workflow_setup_source_latched')
            parent = _directory(self.path.parent, self.descriptors)
            self.observation = _companions(self.path)
            conn = self._connection(self.path, readonly=True)
            self.source = conn
            self.source_identity = _coding_org_identity(conn, expected_org_slug=self.fence.root.name,
                                                        expected_path=str(self.path))
            conn.execute('BEGIN')
            # Pin the snapshot before measuring its checkpoint-equivalent B.
            conn.execute('SELECT count(*) FROM sqlite_schema').fetchone()
            admitted = _companions(self.path)
            # A cleanly closed WAL-mode image has no companions. Native RO
            # snapshot admission may create its empty WAL and SHM; this is not
            # a checkpoint or stored-cell change. Never accept pre-existing
            # companion disappearance/replacement or any committed WAL drift.
            created_empty = (self.observation[1:3] == (None, None)
                             and admitted[1] is not None and admitted[1][5] == 0
                             and admitted[2] is not None and admitted[2][5] == 32768)
            if (admitted[0] != self.observation[0]
                    or (not created_empty and (admitted[1] != self.observation[1]
                        or (admitted[2] is None) != (self.observation[2] is None)
                        or (admitted[2] is not None and admitted[2][:6] != self.observation[2][:6])))):
                raise BootstrapEvidenceError('workflow_setup_source_changed')
            # SQLite's own WAL snapshot admission updates SHM read marks. Only
            # that bounded admission may change its times; main/WAL and SHM
            # inode/type/size remain pinned. Thereafter ALL signatures are exact.
            self.observation = admitted
            pages, size = conn.execute('PRAGMA page_count').fetchone()[0], conn.execute('PRAGMA page_size').fetchone()[0]
            self.image_bytes = pages * size
            self.page_size = size
            if not 0 < self.image_bytes <= 32 * 1024**3:
                raise BootstrapEvidenceError('workflow_setup_backup_bytes_exhausted')
            require_install_integrity(conn, self.integrity, 'source')
            self.layout = inspect_sequential_install(conn, expected_org_slug=self.fence.root.name,
                                                      allowance=self.allowance)
            self.source_inventory = self._inventory(conn)
            raw = json.dumps(self.source_inventory, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf8')
            self.inventory_digest = hashlib.sha256(raw).hexdigest()
            self._unchanged()
            require_bootstrap_idle_history(self)
            _close_descriptor(parent)
        except BaseException:
            self.failed = True
            raise

    def _backup_file(self, attempt: BootstrapAttempt, fd: int) -> tuple:
        attempt._check(reserve=True)
        assert attempt._parent is not None
        info = os.fstat(fd)
        observed = _file(attempt.backup_path)
        if ((info.st_dev, info.st_ino) != observed[:2]
                or observed[:2] == self.observation[0][:2]
                or stat.S_IMODE(info.st_mode) != 0o600
                or not 0 < info.st_size <= 32 * 1024**3):
            raise BootstrapEvidenceError('workflow_setup_backup_unsafe')
        for suffix in ('-wal', '-shm', '-journal'):
            try:
                os.stat('before.db' + suffix, dir_fd=attempt._parent, follow_symlinks=False)
            except FileNotFoundError:
                continue
            raise BootstrapEvidenceError('workflow_setup_backup_sidecar')
        return observed

    def _hash(self, fd: int) -> str:
        value = hashlib.sha256()
        count = 0
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            self._check()
            raw = os.read(fd, 1048576)
            if not raw:
                break
            count += len(raw)
            if count > 32 * 1024**3:
                raise BootstrapEvidenceError('workflow_setup_backup_bytes_exhausted')
            value.update(raw)
        return value.hexdigest()

    def backup(self, attempt: BootstrapAttempt) -> str:
        """Create/fsync/close/reopen/verify before durable backup_verified.

        Sum DB/temp/backup/rehearsal headroom BEFORE creating the backup inode.
        Writer reentry repeats that policy against current free space; neither
        observation reserves blocks or grants checkpoint/writer permission.
        """
        try:
            self._unchanged()
            if (self._backup_started or self.layout is None or self.layout.sequential
                    or attempt.fence is not self.fence or attempt._phase != 'intent'
                    or attempt.source_identity != self.source_identity
                    or attempt.source_inventory_digest != self.inventory_digest
                    or attempt.deadline != self.deadline):
                raise BootstrapEvidenceError('workflow_setup_backup_owner_invalid')
            self._backup_started = True
            attempt._check(reserve=True)
            assert attempt._parent is not None and self.source is not None
            require_bootstrap_space(self, attempt)
            space = os.fstatvfs(attempt._parent)
            if space.f_bavail * space.f_frsize < self.image_bytes + 1024**3:
                raise BootstrapEvidenceError('workflow_setup_backup_space_unavailable')
            fd = os.open('before.db', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=attempt._parent)
            descriptor = dict(fd=fd, attempted=False, closed=False)
            self.descriptors.append(descriptor)
            os.fchmod(fd, 0o600)
            inode = os.fstat(fd)
            if (_file(attempt.backup_path)[:2] != (inode.st_dev, inode.st_ino)
                    or (inode.st_dev, inode.st_ino) == self.observation[0][:2]):
                raise BootstrapEvidenceError('workflow_setup_backup_changed')
            destination = self._connection(attempt.backup_path, readonly=False)
            if _file(attempt.backup_path)[:2] != (inode.st_dev, inode.st_ino):
                raise BootstrapEvidenceError('workflow_setup_backup_changed')
            def progress(status, remaining, total):
                self._unchanged()
                attempt._check(reserve=True)
                # No per-call sleep/retry allowance or hidden replenished wait.
                if status in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                    raise BootstrapEvidenceError('workflow_setup_database_busy')
                if status not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DONE):
                    raise BootstrapEvidenceError('workflow_setup_backup_unavailable')
                if total * self.page_size > 32 * 1024**3:
                    raise BootstrapEvidenceError('workflow_setup_backup_bytes_exhausted')
            self.source.backup(destination, pages=256, progress=progress, sleep=0)
            # A native backup can inherit the WAL header. Normalize only this
            # independent image so its subsequent RO verification needs no
            # sidecar or recovery; never change the source's journal mode.
            if destination.execute('PRAGMA journal_mode=DELETE').fetchone()[0] != 'delete':
                raise BootstrapEvidenceError('workflow_setup_backup_sidecar')
            self._close_connection(destination)
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (inode.st_dev, inode.st_ino):
                raise BootstrapEvidenceError('workflow_setup_backup_changed')
            os.fsync(fd)
            before = self._backup_file(attempt, fd)
            digest = self._hash(fd)
            _close_descriptor(descriptor)
            # Independent native open, physical and complete logical readers.
            backup = self._connection(attempt.backup_path, readonly=True)
            backup.execute('BEGIN')
            require_install_integrity(backup, self.integrity, 'backup')
            layout = inspect_sequential_install(backup, expected_org_slug=self.fence.root.name,
                                                allowance=self.allowance)
            if layout != self.layout or self._inventory(backup) != self.source_inventory:
                raise BootstrapEvidenceError('workflow_setup_backup_inventory_changed')
            self._close_connection(backup)
            check = dict(fd=os.open('before.db', os.O_RDONLY | os.O_NOFOLLOW,
                                    dir_fd=attempt._parent), attempted=False, closed=False)
            self.descriptors.append(check)
            if self._backup_file(attempt, check['fd']) != before or self._hash(check['fd']) != digest:
                raise BootstrapEvidenceError('workflow_setup_backup_changed')
            _close_descriptor(check)
            self._unchanged()
            os.fsync(attempt._parent)
            attempt.publish('backup_verified', backup_sha256=digest)
            self.backup_sha256 = digest
            return digest
        except BaseException:
            self.failed = True
            raise

    def close(self) -> None:
        """Attempt each original connection/descriptor once, preserving causes."""
        errors = []
        for record in reversed(self.connections):
            try:
                self._close_connection(record['connection'])
            except BaseException as exc:
                errors.append(exc)
        for record in reversed(self.descriptors):
            if record['attempted']:
                if not record['closed']:
                    errors.append(BootstrapEvidenceError('workflow_setup_descriptor_close_unknown'))
                continue
            try:
                _close_descriptor(record)
            except BaseException as exc:
                record['error'] = exc
                errors.append(exc)
        if errors:
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_source_close_unknown') from errors[0]
        self.closed = True


def require_bootstrap_idle_history(source: BootstrapSource, *, writer: sqlite3.Connection | None = None) -> None:
    """Complete durable prerequisite under A4:2's original cooperative EX.

    Reuse the native owners' strict histories on the SAME coherent source.
    No Database construction, recovery, attestation, stale-writer exception or
    new participation marker can discharge retained preparation uncertainty.
    The actual writer repeats this before COMMIT on the same cumulative ledger.
    Pre-A1 concurrent daemons remain the documented startup-migration limitation.
    """
    from types import SimpleNamespace
    from runtime.infrastructure.producer_admission import ProducerAdmissionStore, NativeWriterStore
    from runtime.infrastructure.thread_admission import ThreadAdmissionStore
    from runtime.infrastructure.task_pause_controls import decode_journal
    from runtime.infrastructure.db.tasks import TasksMixin, repository_preparation_closed
    from runtime.infrastructure.workflow_schema import _validate_coding_source_idle

    if writer is None and source._history_started:
        raise BootstrapEvidenceError('workflow_setup_history_latched')
    source._unchanged() if writer is None else source._check()
    conn = source.source if writer is None else writer
    if (conn is None or source.layout is None or source.layout.sequential
            or not conn.in_transaction
            or writer is None and conn.execute('PRAGMA query_only').fetchone()[0] != 1
            or writer is not None and not any(record['connection'] is writer and not record['attempted']
                                             for record in source.connections)):
        raise BootstrapEvidenceError('workflow_setup_history_owner_invalid')
    source._history_started = True
    deadline = min(time.monotonic()+5, source.allowance.deadline)
    rows_read = bytes_read = 0

    def check() -> None:
        source._check()
        if time.monotonic() >= deadline:
            raise BootstrapEvidenceError('workflow_setup_history_deadline')

    def progress() -> int:
        source._inventory_steps += 1000
        return int(source._inventory_steps > 640000000 or time.monotonic() >= deadline)

    def acquire(sql: str) -> list[sqlite3.Row]:
        nonlocal rows_read, bytes_read
        result = []
        for row in conn.execute(sql):
            check()
            rows_read += 1
            # Native scalar lengths gate payload acquisition below; replayed
            # rows debit again, rather than borrowing an uncharged collection.
            bytes_read += sum(len(value.encode('utf8')) if type(value) is str else
                              len(value) if type(value) is bytes else 8 for value in row)
            if rows_read > 8192 or bytes_read > 64*1048576:
                raise BootstrapEvidenceError('workflow_setup_history_acquisition_exhausted')
            result.append(row)
        check()
        return result

    try:
        source._inventory_steps += 1000
        if source._inventory_steps > 640000000:
            raise BootstrapEvidenceError('workflow_setup_inventory_exhausted')
        conn.set_progress_handler(progress, 1000)
        check()
        actions = "('producer_move_lifecycle','team_move_native_writer','thread_move_lifecycle','repository_preparation')"
        audit_size = '+'.join('coalesce(length(CAST('+name+' AS BLOB)),0)'
                              for name in ('task_id', 'agent', 'payload'))
        bounds = conn.execute('SELECT count(*),coalesce(sum('+audit_size+'),0),'
            'coalesce(max(length(CAST(payload AS BLOB))),0),'
            "coalesce(sum(typeof(payload)!='text'),0) FROM audit_log WHERE action IN " + actions).fetchone()
        if bounds[0] > 8192 or bounds[1] > 64*1048576 or bounds[2] > 16384 or bounds[3]:
            raise BootstrapEvidenceError('workflow_setup_history_acquisition_exhausted')
        rows_read, bytes_read = bounds[0], bounds[1]
        # Gate native variable-width input in SQL before fetching Python cells.
        # This observation is not a task-count or runtime-capacity qualification.
        pending_rows, pending_bytes = rows_read, bytes_read
        for table, columns in (('thread_invocations', ('thread_id', 'agent_name', 'session_id')),
                               ('task_pause_controls', ('admission_journal_json',))):
            size = '+'.join('coalesce(length(CAST('+name+' AS BLOB)),0)' for name in columns)
            count, total, largest = conn.execute('SELECT count(*),coalesce(sum('+size+'),0),'
                'coalesce(max('+size+'),0) FROM '+table).fetchone()
            pending_rows, pending_bytes = pending_rows+count, pending_bytes+total
            if pending_rows > 8192 or pending_bytes > 64*1048576 or largest > 32*1048576:
                raise BootstrapEvidenceError('workflow_setup_history_acquisition_exhausted')
            check()
        # These original stores need only _conn for reading. The actual native
        # connection is query-only and has no writable Database facade to borrow.
        facade = SimpleNamespace(_conn=conn)
        producers = ProducerAdmissionStore(facade, source.fence.root.name).history_uncommitted()
        if any(not state.producer_settled or state.deferred or state.unknown
               or any(not attempt.settled or not attempt.quiescent for attempt in state.attempts)
               for state in producers.values()):
            raise BootstrapEvidenceError('workflow_setup_history_not_idle')
        writers = NativeWriterStore(facade, source.fence.root.name).history_uncommitted()
        if any(state.state != 'settled' for state in writers.values()):
            raise BootstrapEvidenceError('workflow_setup_history_not_idle')
        # A terminal task label cannot settle even a pre-launch preparation.
        # Closed Pause invocations are removed by their original owner; every
        # remaining entry still needs that owner's positive reconciliation.
        for row in acquire('SELECT admission_journal_json FROM task_pause_controls'):
            if decode_journal(row[0])['entries']:
                raise BootstrapEvidenceError('workflow_setup_history_not_idle')
        invocations = acquire('SELECT id,thread_id,agent_name,session_id FROM thread_invocations ORDER BY id')
        histories = acquire("SELECT id,task_id,agent,payload FROM audit_log WHERE action='thread_move_lifecycle' ORDER BY id")
        by_thread: dict[str, list] = {}
        for row in histories:
            by_thread.setdefault(row['task_id'], []).append(row)
        store = ThreadAdmissionStore(facade, source.fence.root.name)
        native = {row['id']: row for row in invocations}
        for thread, history in by_thread.items():
            for _, state in store._decode_states(thread, history, check=check):
                row = native.get(state.invocation_id)
                if (row is None or (row['thread_id'], row['agent_name'], row['session_id'])
                        != (thread, state.agent_id, state.session_id)):
                    raise BootstrapEvidenceError('workflow_setup_history_not_idle')
        for invocation in invocations:
            history = by_thread.get(invocation['thread_id'], [])
            # Bound complete sibling replay as well as initial acquisition.
            rows_read += len(history)
            bytes_read += sum(len(row['payload'].encode('utf8')) for row in history)
            if rows_read > 8192 or bytes_read > 64*1048576:
                raise BootstrapEvidenceError('workflow_setup_history_acquisition_exhausted')
            states = store._replay_bounded(SimpleNamespace(**dict(invocation)), history, check=check)
            state = states[-1][1]
            if (not state.producer_settled or state.deferred or state.unknown
                    or any(not attempt.settled or not attempt.quiescent for attempt in state.attempts)):
                raise BootstrapEvidenceError('workflow_setup_history_not_idle')
        # Repository commands have independent original worker/outer custody.
        # A terminal native producer or empty Pause journal cannot close them.
        # Scalar-size the WHOLE collection above, then debit each original
        # scoped reader before acquiring its variable-width payloads.
        scopes = acquire('SELECT task_id,count(*),sum('+audit_size+') FROM audit_log '
            "WHERE action='repository_preparation' GROUP BY task_id ORDER BY task_id")
        for scope, count, size in scopes:
            rows_read, bytes_read = rows_read+count, bytes_read+size
            if rows_read > 8192 or bytes_read > 64*1048576:
                raise BootstrapEvidenceError('workflow_setup_history_acquisition_exhausted')
            check()
            history = TasksMixin.repository_preparation_history_uncommitted(facade, scope)
            obligations: dict[str, list] = {}
            for item in history:
                check()
                event = item['event']
                if event['owner']['org'] != source.fence.root.name:
                    raise BootstrapEvidenceError('workflow_setup_history_not_idle')
                obligations.setdefault(event['obligation'], []).append(event)
            for stream in obligations.values():
                check()
                if stream[-1]['phase'] != 'closed' or not repository_preparation_closed(stream):
                    raise BootstrapEvidenceError('workflow_setup_history_not_idle')
            check()
        # Storage validity and native terminal history alone cannot exclude
        # queued/result/recovery work or retained publication/profile owners.
        # Bound the existing idle reader's whole collections BEFORE payload
        # acquisition; debit its validation read as well as this preflight.
        for table, predicate in (
            ('workflow_dispatch_operations', '1'),
            ('workflow_dispatch_outbox', '1'),
            ('workflow_request_task_bridges', '1'),
            ('workflow_publication_journals', '1'),
            ('workflow_authority_pointers', '1'),
            ('workflow_profile_operations', '1'),
            ('task_completion_recoveries', '1'),
            ('workflow_recovery_claims', '1'),
            ('task_pause_controls', '1'),
            ('jobs', "status='pending'"),
        ):
            columns = [row[1] for row in conn.execute('PRAGMA table_info("'+table+'")')]
            size = '+'.join('coalesce(length(CAST("'+name+'" AS BLOB)),0)' for name in columns)
            count, total, largest = conn.execute('SELECT count(*),coalesce(sum('+size+'),0),'
                'coalesce(max('+size+'),0) FROM "'+table+'" WHERE '+predicate).fetchone()
            rows_read, bytes_read = rows_read+2*count, bytes_read+2*total
            if rows_read > 8192 or bytes_read > 64*1048576 or largest > 32*1048576:
                raise BootstrapEvidenceError('workflow_setup_history_acquisition_exhausted')
            check()
        _validate_coding_source_idle(conn, expected_org_slug=source.fence.root.name,
                                     layout=source.layout.document)
        check()
        source._unchanged() if writer is None else source._check()
    except BaseException:
        source.failed = True
        raise
    finally:
        conn.set_progress_handler(None, 0)


class BootstrapRehearsal:
    """One never-served writable FIRST/NEXT image, before source effects.

    The coordinator retains this owner before run(), and must positively close
    it before source writer admission. Private load success proves capacity on
    this candidate only; it cannot settle an original live native owner. No
    copied task is queued, no listener is started, and no validator is replaced.
    """

    def __init__(self, source: BootstrapSource, attempt: BootstrapAttempt) -> None:
        self.source, self.attempt = source, attempt
        self.root: Path | None = None
        self._scratch_identity: tuple | None = None
        self.descriptors: list[dict] = []
        self.connections: list[dict] = []
        self.attachments: list[dict] = []
        self.metadata: list[tuple[Path, tuple | None]] = []
        self._entries = self._bytes = 0
        self.started = self.failed = self.completed = self.closed = False
        self._cleanup_attempted = False
        self.receipt: dict | None = None

    def _check(self) -> None:
        if self.failed or self.closed:
            raise BootstrapEvidenceError('workflow_setup_rehearsal_latched')
        self.source._unchanged()
        self.attempt._check(reserve=True)
        if (self.attempt.fence is not self.source.fence
                or self.attempt.deadline != self.source.deadline
                or self.attempt._phase != 'backup_verified'
                or self.attempt._backup_sha256 != self.source.backup_sha256):
            raise BootstrapEvidenceError('workflow_setup_rehearsal_owner_invalid')

    def _own(self, fd: int) -> dict:
        record = dict(fd=fd, attempted=False, closed=False)
        self.descriptors.append(record)
        return record

    def _copy_file(self, path: Path, destination: Path, *, image: bool = False) -> None:
        self._check()
        parent = _directory(path.parent, self.descriptors)
        original = _file(path)
        limit = 32 * 1024**3 if image else 1048576
        if original[5] > limit:
            raise BootstrapEvidenceError('workflow_setup_rehearsal_copy_exhausted')
        incoming = self._own(os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent['fd']))
        outgoing = self._own(os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600))
        if (os.fstat(incoming['fd']).st_dev, os.fstat(incoming['fd']).st_ino) != original[:2]:
            raise BootstrapEvidenceError('workflow_setup_rehearsal_input_changed')
        digest, copied = hashlib.sha256(), 0
        while True:
            self._check()
            raw = os.read(incoming['fd'], min(1048576, limit-copied+1))
            if not raw:
                break
            copied += len(raw)
            if copied > limit:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_copy_exhausted')
            if not image:
                self._bytes += len(raw)
                if self._bytes > 16 * 1048576:
                    raise BootstrapEvidenceError('workflow_setup_rehearsal_copy_exhausted')
            digest.update(raw)
            remaining = memoryview(raw)
            while remaining:
                self._check()
                written = os.write(outgoing['fd'], remaining)
                if written <= 0:
                    raise BootstrapEvidenceError('workflow_setup_short_write')
                remaining = remaining[written:]
        if (_file(path) != original or copied != original[5]
                or (image and digest.hexdigest() != self.source.backup_sha256)):
            raise BootstrapEvidenceError('workflow_setup_rehearsal_input_changed')
        _close_descriptor(outgoing)
        _close_descriptor(incoming)
        _close_descriptor(parent)
        self.metadata.append((path, original))

    def _copy_metadata(self, path: Path, destination: Path, *, depth: int = 0) -> None:
        self._check()
        if depth > 32:
            raise BootstrapEvidenceError('workflow_setup_rehearsal_copy_exhausted')
        parent = _directory(path, self.descriptors)
        info = os.fstat(parent['fd'])
        signature = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                     info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if info.st_uid != os.getuid():
            raise BootstrapEvidenceError('workflow_setup_rehearsal_input_unsafe')
        self.metadata.append((path, signature))
        destination.mkdir(mode=0o700)
        for name in sorted(_names(parent['fd'], 8192, self.source.allowance.deadline)):
            self._entries += 1
            if self._entries > 8192:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_copy_exhausted')
            entry = os.stat(name, dir_fd=parent['fd'], follow_symlinks=False)
            if stat.S_ISDIR(entry.st_mode):
                self._copy_metadata(path/name, destination/name, depth=depth+1)
            else:
                self._copy_file(path/name, destination/name)
        _close_descriptor(parent)

    def _inputs_unchanged(self) -> None:
        self._check()
        for path, original in self.metadata:
            self._check()
            try:
                info = path.lstat()
            except FileNotFoundError:
                if original is None:
                    continue
                raise BootstrapEvidenceError('workflow_setup_rehearsal_input_changed') from None
            current = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                       info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if current != original:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_input_changed')

    def _connect(self, path: Path) -> sqlite3.Connection:
        self._check()
        conn = sqlite3.connect(path.as_uri()+'?mode=rw', uri=True, timeout=0)
        self.connections.append(dict(connection=conn, attempted=False, closed=False))
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _close_connection(self, record: dict) -> None:
        if record['attempted']:
            if not record['closed']:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_close_unknown')
            return
        record['attempted'] = True
        try:
            record['connection'].close()
        except BaseException as exc:
            record['error'] = exc
            raise
        record['closed'] = True

    def _close_attachment(self, owner: dict) -> None:
        if owner.get('rehearsal_close_attempted'):
            if not owner.get('rehearsal_closed'):
                raise BootstrapEvidenceError('workflow_setup_rehearsal_close_unknown')
            return
        owner['rehearsal_close_attempted'] = True
        try:
            if owner.get('returned') is not None:
                owner['returned'].close()
            elif not owner.get('load_db_closed'):
                # OrgState.load already attempted its original close. Never
                # retry a failed construction/close or infer closure from GC.
                raise BootstrapEvidenceError('workflow_setup_rehearsal_close_unknown')
        except BaseException as exc:
            owner['rehearsal_close_error'] = exc
            raise
        owner['rehearsal_closed'] = True

    def run(self, settings: Settings) -> dict:
        """Install the exact candidate, then actually load writable FIRST/NEXT.

        The original install ledgers/deadline are shared. Both loads use their
        unchanged default budgets, without installer allowances or ready-cache
        seeding. The supervising worker owns the absolute operation deadline;
        all local work is also checked against the pre-COMMIT reserve.
        """
        from runtime.daemon.org_state import OrgState
        from runtime.infrastructure.workflow_schema import install_sequential_schema
        from runtime.workflows.sequential_builtin import inspect_definition, preset_catalog
        from runtime.workflows.sequential_history import _ReadBudget

        try:
            self._check()
            if self.started:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_latched')
            self.started = True
            catalog = tuple(compiled.pin for compiled in preset_catalog())
            temporary = Path(tempfile.gettempdir()).resolve(strict=True)
            if (temporary.is_relative_to(self.attempt.runtime_root)
                    or temporary.is_relative_to(self.attempt.home/'backups')
                    or set(temporary.parts) & {'output', 'artifacts', 'task-attachments'}):
                raise BootstrapEvidenceError('workflow_setup_rehearsal_location_unsafe')
            # Retain the actual scratch location before any fallible setup. No
            # TemporaryDirectory finalizer can delete unknown live handles.
            self.root = Path(tempfile.mkdtemp(prefix='happyranch-workflow-rehearsal-', dir=temporary))
            parent = _directory(self.root, self.descriptors)
            _private_directory(parent['fd'])
            info = os.fstat(parent['fd'])
            self._scratch_identity = info.st_dev, info.st_ino
            space = os.fstatvfs(parent['fd'])
            if space.f_bavail * space.f_frsize < self.source.image_bytes + 64 * 1048576:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_space_unavailable')
            _close_descriptor(parent)
            root = self.root/'runtime/orgs'/self.attempt.slug
            root.mkdir(parents=True, mode=0o700)
            self._copy_metadata(self.source.fence.root/'org', root/'org')
            for name in ('.org_settings_seeded', '.hr_review_renamed'):
                path = self.source.fence.root/name
                try:
                    path.lstat()
                except FileNotFoundError:
                    self.metadata.append((path, None))
                else:
                    self._copy_file(path, root/name)
            path = root/'happyranch.db'
            self._copy_file(self.attempt.backup_path, path, image=True)
            self._inputs_unchanged()
            conn = self._connect(path)
            identity = _coding_org_identity(conn, expected_org_slug=self.attempt.slug, expected_path=str(path))
            result = install_sequential_schema(conn, expected_org_slug=self.attempt.slug,
                expected_path=str(path), source_identity=identity,
                source_inventory=self.source.source_inventory, offline_check=self._inputs_unchanged,
                allowance=self.source.allowance, integrity=self.source.integrity)
            if result != 'installed':
                raise BootstrapEvidenceError('workflow_setup_rehearsal_candidate_invalid')
            self._close_connection(self.connections[-1])
            timings = []
            for phase in ('first', 'next'):
                self._inputs_unchanged()
                owner = dict(startup_acquisition=True, phase=phase)
                self.attachments.append(owner)
                started = time.monotonic()
                org = OrgState.load(slug=self.attempt.slug, root=root,
                                    settings=settings, attachment_owner=owner)
                owner['returned'] = org
                self._check()
                if org._startup_authority_error is not None:
                    raise org._startup_authority_error
                if org._startup_publication_held or org.naming_readiness != 'ready':
                    raise BootstrapEvidenceError('workflow_setup_rehearsal_authority_unavailable')
                # Registration is the actual A2 tail, never a direct fixture
                # INSERT or a success stub. Recheck the same reviewed catalog.
                budget = _ReadBudget(deadline=min(time.monotonic()+5, self.source.allowance.deadline))
                candidates = preset_catalog()
                if tuple(compiled.pin for compiled in candidates) != catalog:
                    raise BootstrapEvidenceError('workflow_setup_rehearsal_candidate_changed')
                for compiled in candidates:
                    if not inspect_definition(org.db._conn, compiled, budget)[1]:
                        raise BootstrapEvidenceError('workflow_setup_rehearsal_catalog_unavailable')
                self._close_attachment(owner)
                timings.append(time.monotonic()-started)
                self._inputs_unchanged()
            self.completed = True
            self.receipt = dict(source_inventory_digest=self.source.inventory_digest,
                backup_sha256=self.source.backup_sha256, target_version=1,
                catalog=tuple(pin.model_dump(mode='json') for pin in catalog),
                first_seconds=timings[0], next_seconds=timings[1])
            return dict(self.receipt)
        except BaseException:
            self.failed = True
            raise

    def close(self) -> None:
        """One original close each; remove scratch only after positive closure."""
        errors = []
        if self.root is not None and not self.closed:
            try:
                parent = _directory(self.root, self.descriptors)
                _private_directory(parent['fd'])
                info = os.fstat(parent['fd'])
                if (info.st_dev, info.st_ino) != self._scratch_identity:
                    raise BootstrapEvidenceError('workflow_setup_rehearsal_scratch_changed')
            except BaseException as exc:
                errors.append(exc)
        for owner in reversed(self.attachments):
            try:
                self._close_attachment(owner)
            except BaseException as exc:
                errors.append(exc)
        for record in reversed(self.connections):
            try:
                self._close_connection(record)
            except BaseException as exc:
                errors.append(exc)
        for record in reversed(self.descriptors):
            if record['attempted']:
                if not record['closed']:
                    errors.append(BootstrapEvidenceError('workflow_setup_descriptor_close_unknown'))
                continue
            try:
                _close_descriptor(record)
            except BaseException as exc:
                record['error'] = exc
                errors.append(exc)
        if errors:
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_rehearsal_close_unknown') from errors[0]
        if self.root is not None and not self.closed:
            if self._cleanup_attempted:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_cleanup_unknown')
            self._cleanup_attempted = True
            shutil.rmtree(self.root)
        self.closed = True


def require_bootstrap_space(source: BootstrapSource, attempt: BootstrapAttempt) -> None:
    """Sum original backup/DB/temp/rehearsal reservations by actual device.

    The supervised helper selects SQLITE_TMPDIR before SQLite work. An unset,
    aliased or unavailable temp volume is not permission to guess a location.
    This is headroom policy, not a guarantee against a later ENOSPC.
    """
    source._check()
    attempt._check(reserve=True)
    temporary = os.environ.get('SQLITE_TMPDIR')
    if not temporary or not Path(temporary).is_absolute():
        raise BootstrapEvidenceError('workflow_setup_temp_volume_unavailable')
    rehearsal = Path(tempfile.gettempdir()).resolve(strict=True)
    if (rehearsal.is_relative_to(attempt.runtime_root)
            or rehearsal.is_relative_to(attempt.home/'backups')
            or set(rehearsal.parts) & {'output', 'artifacts', 'task-attachments'}):
        raise BootstrapEvidenceError('workflow_setup_rehearsal_location_unsafe')
    size = source.image_bytes
    if not 0 < size <= 32 * 1024**3 or source.observation is None:
        raise BootstrapEvidenceError('workflow_setup_backup_bytes_exhausted')
    sidecars = sum(value[5] for value in source.observation[1:] if value is not None)
    database_required = 2*size + 64*1048576 + 1024**3 + sidecars
    requirements: dict[int, int] = {}
    available: dict[int, int] = {}
    database_devices = set()
    for path, category, required in (
            (source.path.parent, 'database', database_required),
            (Path(temporary), 'database', database_required),
            (attempt.root, 'backup', size+1024**3),
            (rehearsal, 'rehearsal', size+64*1048576)):
        source._check()
        parent = _directory(path, source.descriptors)
        try:
            if path == Path(temporary):
                _private_directory(parent['fd'])
                if not os.access(path, os.W_OK | os.X_OK):
                    raise BootstrapEvidenceError('workflow_setup_temp_volume_unavailable')
            info, space = os.fstat(parent['fd']), os.fstatvfs(parent['fd'])
            available[info.st_dev] = min(available.get(info.st_dev, math.inf),
                                         space.f_bavail*space.f_frsize)
            if category != 'database' or info.st_dev not in database_devices:
                requirements[info.st_dev] = requirements.get(info.st_dev, 0) + required
            if category == 'database':
                database_devices.add(info.st_dev)
        finally:
            _close_descriptor(parent)
    if any(available[device] < required for device, required in requirements.items()):
        raise BootstrapEvidenceError('workflow_setup_volume_space_unavailable')
    source._check()


class BootstrapInstall:
    """One original writer after positively closed writable rehearsal.

    The coordinator retains this object before run(). Its parent-permit callable
    is the real supervisor protocol, not a deadline override. This stage neither
    qualifies live native owners nor registers definitions or publishes ready.
    No failure restores a backup, repeats COMMIT or retries an unknown close.
    """

    def __init__(self, source: BootstrapSource, attempt: BootstrapAttempt,
                 rehearsal: BootstrapRehearsal) -> None:
        self.source, self.attempt, self.rehearsal = source, attempt, rehearsal
        self.writer: sqlite3.Connection | None = None
        self.started = self.committed = self.verified = self.failed = False
        self.close_attempted = self.closed = self.unchanged = False
        self.commit_uncertain = False
        self.primary: BaseException | None = None
        self.close_error: BaseException | None = None
        self._permit_requested = False

    def _check(self, *, reserve: bool = True) -> None:
        self.attempt._check(reserve=reserve)
        self.source._check()
        if (self.source.fence is not self.attempt.fence
                or self.source.deadline != self.attempt.deadline
                or self.rehearsal.source is not self.source
                or self.rehearsal.attempt is not self.attempt
                or not self.rehearsal.completed or self.rehearsal.failed
                or not self.rehearsal.closed or self.rehearsal.receipt is None
                or self.rehearsal.receipt['source_inventory_digest'] != self.source.inventory_digest
                or self.rehearsal.receipt['backup_sha256'] != self.source.backup_sha256
                or self.rehearsal.receipt['target_version'] != 1
                or self.attempt._backup_sha256 != self.source.backup_sha256
                or self.attempt.source_identity != self.source.source_identity
                or self.attempt.source_inventory_digest != self.source.inventory_digest
                or _file(self.source.path)[:5] != self.source.observation[0][:5]):
            raise BootstrapEvidenceError('workflow_setup_writer_owner_invalid')

    def _inputs(self) -> None:
        """Revalidate original canonical metadata and the entire verified image."""
        self._check()
        from runtime.workflows.sequential_builtin import preset_catalog
        pins = tuple(compiled.pin.model_dump(mode='json') for compiled in preset_catalog())
        if pins != self.rehearsal.receipt['catalog']:
            raise BootstrapEvidenceError('workflow_setup_rehearsal_candidate_changed')
        for path, original in self.rehearsal.metadata:
            self._check()
            try:
                info = path.lstat()
            except FileNotFoundError:
                if original is None:
                    continue
                raise BootstrapEvidenceError('workflow_setup_rehearsal_input_changed') from None
            current = (info.st_dev, info.st_ino, info.st_mode, info.st_uid, info.st_nlink,
                       info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if current != original:
                raise BootstrapEvidenceError('workflow_setup_rehearsal_input_changed')
        assert self.attempt._parent is not None
        descriptor = dict(fd=os.open('before.db', os.O_RDONLY | os.O_NOFOLLOW,
                                    dir_fd=self.attempt._parent), attempted=False, closed=False)
        self.source.descriptors.append(descriptor)
        before = self.source._backup_file(self.attempt, descriptor['fd'])
        if (self.source._hash(descriptor['fd']) != self.source.backup_sha256
                or self.source._backup_file(self.attempt, descriptor['fd']) != before):
            raise BootstrapEvidenceError('workflow_setup_backup_changed')
        _close_descriptor(descriptor)
        self._check()

    def _checkpoint(self, conn: sqlite3.Connection) -> None:
        self._check(reserve=not self.committed)
        # Known committed WAL only, under the ORIGINAL EX. A journal/alias is
        # refused before SQLite can recover it, never unlinked or repaired.
        _companions(self.source.path)
        if conn.execute('PRAGMA journal_mode').fetchone()[0] == 'wal':
            result = tuple(conn.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone())
            if result[0] != 0 or result[1] != result[2]:
                raise BootstrapEvidenceError('workflow_setup_checkpoint_unavailable')
        self._check(reserve=not self.committed)

    def _default_target(self, conn: sqlite3.Connection) -> None:
        from runtime.workflows.sequential_history import read_sequential_history
        self._check(reserve=not self.committed)
        history = read_sequential_history(conn, expected_org_slug=self.attempt.slug,
            deadline=min(time.monotonic()+5, self.source.allowance.deadline))
        if (not history.layout.sequential or history.layout.ownership != 2
                or (history.layout.document, history.layout.coding, history.layout.owner_indexed)
                != (self.source.layout.document, self.source.layout.coding, self.source.layout.owner_indexed)):
            raise BootstrapEvidenceError('workflow_setup_target_mismatch')
        self._check(reserve=not self.committed)

    def _preserved(self, conn: sqlite3.Connection) -> None:
        from runtime.infrastructure.workflow_sequential_schema import SEQUENTIAL_TABLES
        schema, tables = self.source._inventory(conn)
        old_schema, old_tables = self.source.source_inventory
        replacements = {'workflow_task_ownership', 'workflow_task_owner_bindings'}
        objects = {(row[0], row[1]): row for row in schema}
        if (set(tables) != set(old_tables) | set(SEQUENTIAL_TABLES)
                or any(tables[name] != rows for name, rows in old_tables.items())
                or any(tables[name][0] != (1 if name == SEQUENTIAL_TABLES[0] else 0)
                       for name in SEQUENTIAL_TABLES)
                or any(objects.get((row[0], row[1])) != row for row in old_schema
                       if row[1] not in replacements)):
            raise BootstrapEvidenceError('workflow_setup_retained_inventory_changed')

    def run(self, permit: Callable[[BootstrapAttempt], bool]) -> None:
        """Verify target, fsync pre_commit, obtain ONE permit, commit, reopen.

        The source/backup/rehearsal and actual writer all retain the same install
        ledgers. Target and independent reopen use original default 32M/A-B-C/5s.
        The supervising owner must still bound native/fsync/close and cancellation.
        """
        from runtime.infrastructure.workflow_schema import install_sequential_schema, SequentialInstallUncertain
        if self.started or self.failed or self.closed:
            raise BootstrapEvidenceError('workflow_setup_writer_latched')
        try:
            self.started = True
            self._check()
            if self.attempt._phase != 'backup_verified':
                raise BootstrapEvidenceError('workflow_setup_writer_phase_invalid')
            self.source._unchanged()
            self._inputs()
            require_bootstrap_space(self.source, self.attempt)
            assert self.source.source is not None
            self.source._close_connection(self.source.source)
            self.writer = self.source._connection(self.source.path, readonly=False)
            self.writer.execute('PRAGMA synchronous=FULL')
            if self.writer.execute('PRAGMA synchronous').fetchone()[0] != 2:
                raise BootstrapEvidenceError('workflow_setup_durability_unavailable')
            # Never checkpoint before COMMIT: a later failed gate must leave
            # the original DB bytes unchanged, including an existing WAL source.
            # Writer reentry uses the same identity/inventory under
            # BEGIN IMMEDIATE, and the primitive repeats writer-source integrity.
            def guard():
                self._inputs()
                if _coding_org_identity(self.writer, expected_org_slug=self.attempt.slug,
                                       expected_path=str(self.source.path)) != self.source.source_identity:
                    raise BootstrapEvidenceError('workflow_setup_source_changed')
            def before_commit():
                self._default_target(self.writer)
                self._preserved(self.writer)
                guard()
                require_bootstrap_idle_history(self.source, writer=self.writer)
                self.attempt.publish('pre_commit')
                self._check()
                if self._permit_requested:
                    raise BootstrapEvidenceError('workflow_setup_commit_permit_repeated')
                self._permit_requested = True
                if permit(self.attempt) is not True:
                    raise BootstrapEvidenceError('workflow_setup_commit_permit_refused')
                # Parent may spend time deciding. No permit replenishes the
                # absolute deadline or waives the final twenty-second reserve.
                self._check()
            result = install_sequential_schema(self.writer, expected_org_slug=self.attempt.slug,
                expected_path=str(self.source.path), source_identity=self.source.source_identity,
                source_inventory=self.source.source_inventory, offline_check=guard,
                allowance=self.source.allowance, integrity=self.source.integrity, before_commit=before_commit)
            if result != 'installed':
                raise BootstrapEvidenceError('workflow_setup_writer_target_changed')
            self.committed = True
            # Durable committed BEFORE any fallible original writer close.
            self.attempt.publish('committed')
            self.source._close_connection(self.writer)
            checkpoint = self.source._connection(self.source.path, readonly=False)
            self._checkpoint(checkpoint)
            self.source._close_connection(checkpoint)
            reopened = self.source._connection(self.source.path, readonly=True)
            reopened.execute('BEGIN')
            self._default_target(reopened)
            self._preserved(reopened)
            self.source._close_connection(reopened)
            self.close_attempted = True
            self.source.close()
            self.closed = True
            self.attempt.publish('verified')
            self.verified = True
        except BaseException as exc:
            self.failed, self.primary = True, exc
            self.commit_uncertain = self.committed or isinstance(exc, SequentialInstallUncertain)
            raise

    def close(self) -> None:
        """Close originals once; record unchanged only after independent proof.

        COMMIT/rollback/close uncertainty never enters the unchanged branch.
        A terminal phase is not an automatic retry or legacy-load instruction.
        """
        if self.close_attempted:
            if not self.closed:
                raise BootstrapEvidenceError('workflow_setup_writer_close_unknown')
            return
        self.close_attempted = True
        proof_error = None
        original_equal = False
        try:
            # Do not reuse a failed writer/snapshot as the preservation oracle.
            errors = []
            for record in reversed(self.source.connections):
                try:
                    self.source._close_connection(record['connection'])
                except BaseException as exc:
                    errors.append(exc)
            if errors:
                raise BootstrapEvidenceError('workflow_setup_writer_close_unknown') from errors[0]
            if self.failed and not self.commit_uncertain:
                self._check()
                _companions(self.source.path)
                proof = self.source._connection(self.source.path, readonly=True)
                proof.execute('BEGIN')
                if (_coding_org_identity(proof, expected_org_slug=self.attempt.slug,
                        expected_path=str(self.source.path)) != self.source.source_identity
                        or inspect_sequential_install(proof, expected_org_slug=self.attempt.slug,
                            allowance=self.source.allowance) != self.source.layout
                        or self.source._inventory(proof) != self.source.source_inventory):
                    raise BootstrapEvidenceError('workflow_setup_rollback_unproven')
                self.source._close_connection(proof)
                original_equal = True
        except BaseException as exc:
            proof_error = exc
        try:
            # Even failed equality/identity/reader proof must close all other
            # original handles. Unknown native closes remain latched in their
            # records; this method never repeats the actual close behind them.
            self.source.close()
            self.closed = True
            if proof_error is not None:
                raise proof_error
            if original_equal:
                self.attempt.publish('not_installed')
                self.unchanged = True
        except BaseException as exc:
            self.close_error = exc
            raise


class BootstrapRegistration:
    """Actual A2 registrar under a duplicate of the original EX, never served.

    Successful definition registration, positive original closure and the
    machine audit precede registered. An uncertain tail is never replayed.
    """

    def __init__(self, attempt: BootstrapAttempt, settings: Settings) -> None:
        self.attempt, self.settings = attempt, settings
        self.owner: dict = {'startup_acquisition': True}
        self.audit_owner: dict = {}
        self.descriptors: list[dict] = []
        self.org = self.fence = self.audit = None
        self.started = self.closed = self.failed = False

    def run(self) -> None:
        from runtime.daemon.org_state import OrgState
        from runtime.infrastructure.database import Database
        from runtime.workflows.sequential_builtin import preset_catalog, inspect_definition
        from runtime.workflows.sequential_history import _ReadBudget

        if self.started:
            raise BootstrapEvidenceError('workflow_setup_registration_latched')
        self.started = True
        attempt = self.attempt
        attempt._check()
        if attempt._phase != 'verified':
            raise BootstrapEvidenceError('workflow_setup_registration_unavailable')
        for fd in (attempt.fence.fd, attempt.fence._parent):
            self.descriptors.append(dict(fd=os.dup(fd), attempted=False, closed=False))
        self.fence = OrgAccessFence(self.descriptors[0]['fd'], self.descriptors[1]['fd'],
            attempt.fence._name, attempt.fence.root, True, attempt.fence._namespace)
        self.org = OrgState.load(slug=attempt.slug, root=attempt.fence.root,
            settings=self.settings, attachment_owner=self.owner, access_fence=self.fence,
            bootstrap_registration=attempt)
        attempt._check()
        if (self.org._startup_publication_held or self.org._startup_authority_error is not None
                or self.org.naming_readiness != 'ready'):
            raise BootstrapEvidenceError('workflow_setup_registration_unavailable')
        with self.org.db.workflow_schema_transaction() as conn:
            budget = _ReadBudget(deadline=min(time.monotonic()+5, attempt.deadline))
            if any(not inspect_definition(conn, compiled, budget)[1] for compiled in preset_catalog()):
                raise BootstrapEvidenceError('workflow_setup_registration_unavailable')
        self._close_org()
        attempt._check()
        self.audit = Database(attempt.home/'runtime-audit.db', construction_owner=self.audit_owner,
                              acquisition_timeout=0)
        self.audit.insert_audit_log(task_id='', agent='daemon', action='workflow_setup_registered',
            payload=dict(format='workflow-setup-registered@1', runtime_key=attempt.runtime_key,
                org_slug=attempt.slug, attempt_id=attempt.attempt_id,
                backup_sha256=attempt._backup_sha256, target_version=1))

    def _close_org(self) -> None:
        if self.owner.get('registration_close_attempted'):
            if not self.owner.get('registration_closed'):
                raise BootstrapEvidenceError('workflow_setup_registration_close_unknown')
            return
        self.owner['registration_close_attempted'] = True
        if self.org is not None:
            self.org.close()
        elif self.owner.get('db_construction_started') and not self.owner.get('load_db_closed'):
            raise BootstrapEvidenceError('workflow_setup_registration_close_unknown')
        if self.fence is not None and self.fence._closed:
            for record in self.descriptors:
                record['attempted'] = record['closed'] = True
        else:
            for record in reversed(self.descriptors):
                _close_descriptor(record)
        self.owner['registration_closed'] = True

    def close(self) -> None:
        if self.closed:
            return
        if self.failed:
            raise BootstrapEvidenceError('workflow_setup_registration_close_unknown')
        errors = []
        try:
            self._close_org()
        except BaseException as exc:
            errors.append(exc)
        if not self.audit_owner.get('audit_close_attempted'):
            self.audit_owner['audit_close_attempted'] = True
            try:
                if self.audit is not None:
                    self.audit.close()
                elif self.audit_owner.get('load_db') is not None and not self.audit_owner.get('load_db_closed'):
                    raise BootstrapEvidenceError('workflow_setup_registration_close_unknown')
            except BaseException as exc:
                errors.append(exc)
        if errors:
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_registration_close_unknown') from errors[0]
        self.closed = True


_PIPE_LIMIT = 16384
_WORKER_REASONS = frozenset({'storage_verified', 'org_database_busy', 'org_database_corrupt',
    'org_database_full', 'org_database_interrupted', 'org_database_io', 'org_database_unavailable',
    'org_filesystem_unavailable', 'org_load_refused', 'org_startup_unavailable',
    'default_load_budget_exceeded', 'writer_exclusion_unproven', 'history_check_failed',
    'integrity_check_failed', 'backup_verification_failed', 'registration_failed', 'commit_uncertain'})


def _worker_failure_reason(primary: BaseException, rehearsal: BootstrapRehearsal | None,
                           writer: BootstrapInstall | None) -> str:
    from runtime.daemon.state import _startup_failure_reason
    from runtime.workflows._read_exhaustion import exhausted
    from runtime.infrastructure.workflow_integrity import InstallIntegrityError
    if writer is not None and writer.commit_uncertain:
        return 'commit_uncertain'
    if isinstance(primary, InstallIntegrityError):
        return 'backup_verification_failed' if primary.stage == 'backup' else 'integrity_check_failed'
    if isinstance(primary, OrgAccessHeld):
        return 'writer_exclusion_unproven'
    if isinstance(primary, BootstrapEvidenceError) and str(primary).startswith('workflow_setup_backup'):
        return 'backup_verification_failed'
    if isinstance(primary, BootstrapEvidenceError) and str(primary).startswith('workflow_setup_history'):
        return 'history_check_failed'
    if isinstance(primary, ValueError) and str(primary).startswith((
            'workflow_coding_source_owner_', 'workflow_submission_source_owner_')):
        return 'history_check_failed'
    if ((rehearsal is not None and not rehearsal.completed
            or writer is not None and not writer.commit_uncertain) and exhausted(primary)):
        return 'default_load_budget_exceeded'
    return _startup_failure_reason(primary) if isinstance(primary, Exception) else 'org_startup_unavailable'


def _wire(value: dict) -> bytes:
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8') + b'\n'
    if len(raw) > _PIPE_LIMIT:
        raise BootstrapEvidenceError('workflow_setup_transport_exhausted')
    return raw


def _unwire(raw: bytes) -> dict:
    def unique(items: list[tuple[str, object]]) -> dict:
        value = {}
        for key, item in items:
            if key in value:
                raise BootstrapEvidenceError('workflow_setup_transport_invalid')
            value[key] = item
        return value
    if len(raw) > _PIPE_LIMIT or not raw.endswith(b'\n') or b'\n' in raw[:-1]:
        raise BootstrapEvidenceError('workflow_setup_transport_invalid')
    value = json.loads(raw, object_pairs_hook=unique)
    if type(value) is not dict:
        raise BootstrapEvidenceError('workflow_setup_transport_invalid')
    return value


class BootstrapControl:
    """Child-owned original parent channel; EOF permanently revokes effects.

    The inherited private socket is never reconnected. Every source/phase check
    and the actual writer's final check observes it. Native calls remain
    cooperative; the parent separately bounds and retains the original process.
    """

    def __init__(self, channel: socket.socket, deadline: float) -> None:
        self.channel, self.deadline = channel, deadline
        self.failed = self.permit_requested = False
        self.source_key: dict | None = None

    def check(self) -> None:
        if self.failed or time.monotonic() >= self.deadline:
            raise BootstrapEvidenceError('workflow_setup_parent_unavailable')
        try:
            self.channel.setblocking(False)
            pending = self.channel.recv(1, socket.MSG_PEEK | socket.MSG_DONTWAIT)
        except BlockingIOError:
            return
        except OSError:
            pending = b''
        self.failed = True
        # Either EOF or unsolicited/duplicate permission is a permanent stop.
        raise BootstrapEvidenceError('workflow_setup_parent_unavailable')

    def send(self, value: dict) -> None:
        try:
            self.channel.settimeout(max(.001, self.deadline-time.monotonic()))
            self.channel.sendall(_wire(value))
        except BaseException:
            self.failed = True
            raise

    def receive(self) -> dict:
        raw = bytearray()
        try:
            while len(raw) <= _PIPE_LIMIT:
                remaining = self.deadline-time.monotonic()
                if remaining <= 0:
                    break
                self.channel.settimeout(remaining)
                piece = self.channel.recv(1)
                if not piece:
                    break
                raw.extend(piece)
                if piece == b'\n':
                    return _unwire(bytes(raw))
        except BaseException:
            self.failed = True
            raise
        self.failed = True
        raise BootstrapEvidenceError('workflow_setup_parent_unavailable')

    def begin(self, source: BootstrapSource) -> None:
        self.check()
        self.source_key = dict(source_identity=list(source.source_identity),
                               source_inventory_digest=source.inventory_digest)
        self.send(dict(event='source', **self.source_key))
        if self.receive() != dict(command='begin', **self.source_key):
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_parent_unavailable')
        self.check()

    def permit(self, attempt: BootstrapAttempt) -> bool:
        self.check()
        if (self.permit_requested or self.source_key is None or attempt._phase != 'pre_commit'
                or self.deadline != attempt.deadline):
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_commit_permit_repeated')
        self.permit_requested = True
        key = dict(attempt_id=attempt.attempt_id, backup_sha256=attempt._backup_sha256,
                   **self.source_key)
        self.send(dict(event='pre_commit', **key))
        if self.receive() != dict(command='commit', **key):
            self.failed = True
            raise BootstrapEvidenceError('workflow_setup_commit_permit_refused')
        self.check()
        return True


def bootstrap_helper_main(argv: list[str]) -> int:
    """Private pre-daemon child. No live OrgState, producers or listener.

    It cannot allocate intent until its ORIGINAL parent qualifies the observed
    source, or COMMIT without the same parent's single bound permit. A private
    rehearsal uses the existing actual writable OrgState first/next owner.
    Qualified registration, positive closure, runtime audit and registered phase
    complete the child's original EX interval before ordinary parent attachment.
    """
    if len(argv) != 3 or any(not value.isascii() or not value.isdecimal() for value in argv):
        return 2
    channel_fd, fence_fd, directory_fd = map(int, argv)
    if min(channel_fd, fence_fd, directory_fd) < 3 or len({channel_fd, fence_fd, directory_fd}) != 3:
        return 2
    channel = socket.socket(fileno=channel_fd)
    # Even malformed request acquisition cannot hang an unowned bootstrap child.
    control = BootstrapControl(channel, time.monotonic()+5)
    source = attempt = rehearsal = writer = registration = fence = None
    sqlite_temporary = sqlite_identity = None
    state, closed, primary = 'unknown', False, None
    try:
        request = control.receive()
        if set(request) != {'home', 'root', 'deadline', 'database_identity', 'settings'}:
            raise BootstrapEvidenceError('workflow_setup_transport_invalid')
        deadline = request['deadline']
        if (type(deadline) not in (int, float) or not math.isfinite(deadline)
                or not 20 < deadline-time.monotonic() <= 120):
            raise BootstrapEvidenceError('workflow_setup_deadline')
        control.deadline = deadline
        home, root = Path(request['home']), Path(request['root'])
        from runtime.runtime import daemon_home
        if (home != daemon_home()
                or root.parent.name != 'orgs' or not root.is_absolute()
                or re.fullmatch('[a-z0-9-]{1,40}', root.name) is None):
            raise BootstrapEvidenceError('workflow_setup_owner_invalid')
        from runtime.infrastructure.org_access_fence import org_access_key
        fence = OrgAccessFence(fence_fd, directory_fd, org_access_key(root)+'.lock',
                               root, True, home/'org-access-fences')
        fence.assert_held()
        if list(fence.database_identity or ()) != request['database_identity']:
            raise BootstrapEvidenceError('workflow_setup_source_changed')
        if type(request['settings']) is not dict or set(request['settings']) != set(Settings.model_fields):
            raise BootstrapEvidenceError('workflow_setup_transport_invalid')
        settings = Settings(**request['settings'])
        # Select the actual SQLite temp volume before this helper's FIRST SQL.
        # Keep it out of runtime/backup/output; unknown closure preserves it.
        temporary = Path(tempfile.gettempdir()).resolve(strict=True)
        if (temporary.is_relative_to(root.parent.parent) or temporary.is_relative_to(home/'backups')
                or set(temporary.parts) & {'output', 'artifacts', 'task-attachments'}):
            raise BootstrapEvidenceError('workflow_setup_rehearsal_location_unsafe')
        sqlite_temporary = Path(tempfile.mkdtemp(prefix='happyranch-workflow-sqlite-', dir=temporary))
        info = sqlite_temporary.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise BootstrapEvidenceError('workflow_setup_temp_volume_unavailable')
        sqlite_identity = info.st_dev, info.st_ino
        os.environ['SQLITE_TMPDIR'] = str(sqlite_temporary)
        source = BootstrapSource(fence=fence, deadline=deadline, control=control)
        source.open()
        control.begin(source)
        attempt = BootstrapAttempt(home=home, runtime_root=root.parent.parent, slug=root.name,
            fence=fence, source_identity=source.source_identity,
            source_inventory_digest=source.inventory_digest, deadline=deadline, control=control)
        attempt.begin()
        source.backup(attempt)
        rehearsal = BootstrapRehearsal(source, attempt)
        rehearsal.run(settings)
        rehearsal.close()
        writer = BootstrapInstall(source, attempt, rehearsal)
        writer.run(control.permit)
        registration = BootstrapRegistration(attempt, settings)
        registration.run()
        registration.close()
        attempt.publish('registered')
        state = 'verified'
    except BaseException as exc:
        primary = exc
    finally:
        errors = []
        for owner in (registration, writer, rehearsal, source, attempt):
            if owner is not None:
                try:
                    owner.close()
                except BaseException as exc:
                    errors.append(exc)
        try:
            if fence is not None:
                fence.close()
            else:
                # Own inherited originals even if request validation or fence
                # construction failed before an object could be assigned.
                for fd in (fence_fd, directory_fd):
                    try:
                        os.close(fd)
                    except BaseException as exc:
                        errors.append(exc)
        except BaseException as exc:
            errors.append(exc)
        if not errors and sqlite_temporary is not None:
            try:
                info = sqlite_temporary.lstat()
                if (not stat.S_ISDIR(info.st_mode) or (info.st_dev, info.st_ino) != sqlite_identity
                        or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700):
                    raise BootstrapEvidenceError('workflow_setup_temp_volume_unavailable')
                shutil.rmtree(sqlite_temporary)
            except BaseException as exc:
                errors.append(exc)
        closed = not errors
        if primary is not None:
            state = ('not_started' if attempt is None and closed else
                     'not_installed' if writer is not None and writer.unchanged and closed else 'unknown')
        if not closed:
            state = 'unknown'
        result = dict(event='result', state=state, closed=closed,
                      reason='storage_verified' if primary is None and state == 'verified' else
                             'registration_failed' if registration is not None else
                             _worker_failure_reason(primary if primary is not None else errors[0], rehearsal, writer),
                      attempt_id=None if attempt is None else attempt.attempt_id)
        try:
            # A closed failure report is permitted after parent EOF. No new
            # success event is emitted after revocation/deadline or failed close.
            if state == 'verified':
                control.check()
            control.send(result)
        except BaseException:
            state = 'unknown'
        channel.close()
    return 0 if state == 'verified' else 1


class BootstrapSupervisor:
    """One original subprocess/EX interval, including delayed spawn and reap.

    The coordinator rechecks original cooperative EX and source binding before
    intent and COMMIT under A4:2 startup-migration trust. The helper's verified
    return also requires qualified registration, runtime audit and final phase.
    """

    def __init__(self, discovery: BootstrapDiscovery, settings: Settings) -> None:
        self.discovery, self.settings = discovery, settings
        self.spawn = self.process = self.exchange = None
        self.reap_task = None
        self.channel = self.child_channel = None
        self.source_key: dict | None = None
        self.attempt_id: str | None = None
        self.result: dict | None = None
        self.started = self.expired = self.permit_sent = self.reaped = self.closed = False
        self.primary: BaseException | None = None
        self.cleanup_errors: list[BaseException] = []

    def _check(self, *, reserve: bool = True) -> None:
        route = self.discovery
        if (self.expired or route.failed or route.closed or route.result != 'absent'
                or route.fence is None or not route.fence.exclusive
                or reserve and self.process is not None and self.process.returncode is not None
                or time.monotonic() >= route.deadline-(20 if reserve else 0)):
            raise BootstrapEvidenceError('workflow_setup_worker_unavailable')
        route.fence.assert_held()

    def _withdraw(self) -> None:
        self.expired = True
        if self.channel is not None:
            try:
                self.channel.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _signal(self, sig: int) -> None:
        import asyncio
        if self.process is None and self.spawn is not None and self.spawn.done() and not self.spawn.cancelled():
            try:
                self.process = self.spawn.result()
            except BaseException:
                return
        if self.process is not None:
            if self.reap_task is None:
                self.reap_task = asyncio.create_task(self.process.wait())
                self.reap_task.add_done_callback(lambda _: setattr(self, 'reaped', self.process.returncode is not None))
            if self.process.returncode is not None:
                self.reaped = True
                return
            try:
                os.killpg(self.process.pid, sig)
            except ProcessLookupError:
                pass
            except OSError as exc:
                self.cleanup_errors.append(exc)

    async def run(self, qualify: Callable[[tuple, str], Awaitable[bool]]) -> dict:
        """Bounded original execution; cancellation withdraws, drains, then kills.

        Only literal True from the coordinator's A4:2 EX/source recheck permits
        effects; transport alone grants none. No failed run repeats.
        """
        import asyncio
        import signal
        import sys
        if self.started:
            raise BootstrapEvidenceError('workflow_setup_worker_latched')
        self.started = True
        self._check()
        route, loop = self.discovery, asyncio.get_running_loop()
        if any(worker.started and not worker.reaped for worker in route.startup.workers):
            raise BootstrapEvidenceError('workflow_setup_original_worker_in_flight')
        route.startup.workers.append(self)
        try:
            self.channel, self.child_channel = socket.socketpair()
            self.channel.setblocking(False)
            entry = [] if getattr(sys, 'frozen', False) else ['-m', 'runtime.daemon']
            args = [sys.executable, *entry, '--workflow-sequential-bootstrap-helper',
                    str(self.child_channel.fileno()), str(route.fence.fd), str(route.fence._parent)]
            self.spawn = asyncio.create_task(asyncio.create_subprocess_exec(*args,
                pass_fds=(self.child_channel.fileno(), route.fence.fd, route.fence._parent),
                stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL, start_new_session=True))
        except BaseException as exc:
            self.primary, self.expired = exc, True
            self.reaped = self.spawn is None  # positively no native launch
            for channel in (self.channel, self.child_channel):
                if channel is not None:
                    try:
                        channel.close()
                    except BaseException as error:
                        self.cleanup_errors.append(error)
            self.closed = not self.cleanup_errors and self.reaped
            raise

        async def send(value: dict) -> None:
            await loop.sock_sendall(self.channel, _wire(value))

        async def receive() -> dict:
            raw = bytearray()
            while len(raw) <= _PIPE_LIMIT:
                piece = await loop.sock_recv(self.channel, 1)
                if not piece:
                    raise BootstrapEvidenceError('workflow_setup_worker_transport_lost')
                raw.extend(piece)
                if piece == b'\n':
                    return _unwire(bytes(raw))
            raise BootstrapEvidenceError('workflow_setup_transport_exhausted')

        async def exchange() -> dict:
            self.process = await asyncio.shield(self.spawn)
            if self.reap_task is None:
                self.reap_task = asyncio.create_task(self.process.wait())
            self.child_channel.close()
            self._check()
            await send(dict(home=str(route.startup.home), root=str(route.root), deadline=route.deadline,
                database_identity=list(route.fence.database_identity or ()),
                settings=self.settings.model_dump(mode='json')))
            stage = 'source'
            while True:
                value = await receive()
                if value.get('event') == 'result':
                    if (set(value) != {'event', 'state', 'closed', 'attempt_id', 'reason'}
                            or type(value['closed']) is not bool
                            or value['state'] not in ('not_started', 'not_installed', 'unknown', 'verified')
                            or type(value['reason']) is not str or value['reason'] not in _WORKER_REASONS
                            or (value['reason'] == 'storage_verified') != (value['state'] == 'verified')
                            or value['attempt_id'] is not None and (type(value['attempt_id']) is not str
                                or re.fullmatch('[a-f0-9]{32}', value['attempt_id']) is None)
                            or value['state'] == 'verified' and (stage != 'result' or not value['closed']
                                or value['attempt_id'] != self.attempt_id)):
                        raise BootstrapEvidenceError('workflow_setup_transport_invalid')
                    self.result = value
                    await asyncio.shield(self.reap_task)
                    self.reaped = True
                    self.closed = value['closed']
                    if (value['state'] != 'verified' or self.process.returncode != 0 or self.expired):
                        raise BootstrapEvidenceError('workflow_setup_worker_unverified')
                    self._check(reserve=False)
                    return dict(value)
                self._check()
                if stage == 'source' and set(value) == {'event', 'source_identity', 'source_inventory_digest'} and value['event'] == stage:
                    identity, digest = value['source_identity'], value['source_inventory_digest']
                    if (type(identity) is not list or len(identity) != 4
                            or identity[0] != str(route.root/'happyranch.db')
                            or any(type(item) is not int or item < 0 for item in identity[1:])
                            or tuple(identity[1:3]) != route.fence.database_identity
                            or type(digest) is not str or re.fullmatch('[a-f0-9]{64}', digest) is None):
                        raise BootstrapEvidenceError('workflow_setup_transport_invalid')
                    self.source_key = dict(source_identity=identity, source_inventory_digest=digest)
                    if await qualify(tuple(identity), digest) is not True:
                        raise BootstrapEvidenceError('writer_exclusion_unproven')
                    self._check()
                    await send(dict(command='begin', **self.source_key))
                    stage = 'pre_commit'
                elif (stage == 'pre_commit' and set(value) == {'event', 'attempt_id', 'backup_sha256', *self.source_key}
                        and value['event'] == stage and all(value[key] == item for key, item in self.source_key.items())
                        and type(value['attempt_id']) is str and re.fullmatch('[a-f0-9]{32}', value['attempt_id'])
                        and type(value['backup_sha256']) is str and re.fullmatch('[a-f0-9]{64}', value['backup_sha256'])):
                    if self.permit_sent:
                        raise BootstrapEvidenceError('workflow_setup_commit_permit_repeated')
                    if await qualify(tuple(self.source_key['source_identity']), self.source_key['source_inventory_digest']) is not True:
                        raise BootstrapEvidenceError('writer_exclusion_unproven')
                    self._check()
                    self.attempt_id = value['attempt_id']
                    self.permit_sent = True
                    await send(dict(command='commit', **{key: item for key, item in value.items() if key != 'event'}))
                    stage = 'result'
                else:
                    raise BootstrapEvidenceError('workflow_setup_transport_invalid')

        self.exchange = asyncio.create_task(exchange())
        self.exchange.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        try:
            # Pre-COMMIT work ends at the original reserve. A granted permit
            # permits only its existing closure tail, never another operation.
            while not self.exchange.done():
                end = route.deadline-(2 if self.permit_sent else 20)
                if time.monotonic() >= end:
                    raise BootstrapEvidenceError('workflow_setup_worker_deadline')
                await asyncio.wait({self.exchange}, timeout=min(.05, end-time.monotonic()))
            return self.exchange.result()
        except BaseException as exc:
            self.primary = exc
            self._withdraw()
            # Original cleanup reserve, clipped on early cancellation. Spawn
            # completion itself may be stuck; retain it and kill its late child.
            cleanup = min(route.deadline, time.monotonic()+20)
            if not self.spawn.done():
                self.spawn.add_done_callback(lambda _: self._signal(signal.SIGKILL) if self.expired else None)
            # Await original process closure even if the exchange itself failed
            # (for example a refused qualification). EOF permits owned cleanup.
            drain = self.reap_task if self.reap_task is not None else self.spawn
            await asyncio.wait({drain}, timeout=max(0, cleanup-2-time.monotonic()))
            self._signal(signal.SIGTERM)
            if self.process is not None and self.process.returncode is None:
                await asyncio.wait({self.reap_task}, timeout=max(0, min(.1, cleanup-time.monotonic())))
            self._signal(signal.SIGKILL)
            if self.process is not None:
                await asyncio.wait({self.reap_task}, timeout=max(0, cleanup-time.monotonic()))
                self.reaped = self.process.returncode is not None
            route.fence.retain_unknown()
            raise
        finally:
            # Never close the child-side descriptor before an unresolved spawn
            # consumes it. Parent EX remains owned by the discovery/coordinator.
            if self.spawn.done():
                self.child_channel.close()
            else:
                self.spawn.add_done_callback(lambda _: self.child_channel.close())
            if self.exchange.done():
                self.channel.close()
            else:
                self.exchange.add_done_callback(lambda _: self.channel.close())
