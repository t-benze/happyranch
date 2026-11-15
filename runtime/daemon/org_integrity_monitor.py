"""Daily physical observations, independent of sweep and business admission.

Only the fixed read helper opens a checker connection. Blocking discovery,
receipt publication and audit mirroring have separate process custody, so a
stalled syscall cannot occupy the event loop or an executor shutdown join.
Filesystem bookends are observations, not a fence against same-user renames.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
import re
import signal
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime.daemon._disk_sweep_fs import Expired, directory, identity, signature
from runtime.infrastructure.workflow_integrity import IntegrityLedger, check_full_integrity

LOG = logging.getLogger(__name__)
READ_SECONDS = 60.0
CLEANUP_SECONDS = 2.0
PIPE_LIMIT = 16 * 1024
REPORT_LIMIT = 4096
ACTION = 'workflow_integrity_check'
FORMAT = 'workflow-integrity-report@1'
_SLUG = re.compile(r'[a-z0-9-]{1,40}\Z')
_DATE = re.compile(r'\d{4}-\d{2}-\d{2}\Z')
_REASONS = {'full_integrity_ok', 'full_integrity_not_ok', 'database_corrupt',
            'busy', 'deadline', 'sql_budget', 'open_failed', 'io',
            'identity_changed', 'unattached_unreadable', 'interrupted',
            'in_flight_reader', 'report_unavailable'}
_STAGES = {'open', 'snapshot', 'integrity', 'close', 'supervision', 'receipt'}


def _json(value: Any, limit: int = PIPE_LIMIT) -> bytes:
    body = json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=True, allow_nan=False).encode('ascii')
    if len(body) > limit:
        raise ValueError('record_too_large')
    return body


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def runtime_key(root: Path) -> str:
    return hashlib.sha256(os.fsencode(os.path.abspath(root))).hexdigest()


def helper_argv(kind: str) -> list[str]:
    if kind not in ('read', 'storage', 'inventory'):
        raise ValueError('unknown_integrity_helper')
    entry = [] if getattr(sys, 'frozen', False) else ['-m', 'runtime.daemon']
    return [sys.executable, *entry, '--workflow-integrity-helper', kind]


def _observation(reason: str, stage: str, *, steps: int = 0,
                 digest: str | None = None) -> dict:
    outcome = ('ok' if reason == 'full_integrity_ok' else
               'failed' if reason in ('full_integrity_not_ok', 'database_corrupt') else
               'unavailable')
    return dict(outcome=outcome, reason=reason, stage=stage,
                steps=steps, snapshot_identity=digest)


def _db_identity(root: Path, deadline: float) -> tuple:
    with directory(root, deadline) as parent:
        info = os.stat('happyranch.db', dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError('identity_changed')
        return identity(os.fstat(parent)), identity(info)


def read_snapshot(root: Path, deadline: float) -> dict:
    """One literal, live RO snapshot; no initializer, upgrade or logical gate."""
    started = time.monotonic()
    admission = min(deadline, started + .5)
    ledger = IntegrityLedger(deadline)
    conn = None
    digest = None
    stage = 'open'
    result = _observation('open_failed', stage)
    try:
        before = _db_identity(root, admission)
        conn = sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=ro',
                               uri=True, timeout=max(0, admission - time.monotonic()))
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        conn.execute('PRAGMA query_only=ON')
        if conn.execute('PRAGMA query_only').fetchone() != (1,):
            raise ValueError('open_failed')
        stage = 'snapshot'
        # Connect and snapshot acquisition share ONE cumulative busy allowance.
        conn.execute('PRAGMA busy_timeout=' + str(max(0, int((admission - time.monotonic()) * 1000))))
        conn.execute('BEGIN')
        conn.execute('SELECT rootpage FROM sqlite_schema LIMIT 1').fetchone()
        conn.execute('PRAGMA busy_timeout=0')
        if time.monotonic() > admission:
            result = _observation('busy', stage)
        elif _db_identity(root, deadline) != before:
            result = _observation('identity_changed', stage)
        else:
            digest = hashlib.sha256(_json(before)).hexdigest()
            stage = 'integrity'
            checked = check_full_integrity(conn, ledger)
            reason = checked.cause if checked.cause in _REASONS else 'io'
            result = _observation(reason, stage, steps=ledger.consumed, digest=digest)
            if _db_identity(root, deadline) != before:
                result = _observation('identity_changed', stage, steps=ledger.consumed)
    except sqlite3.Error as exc:
        code = getattr(exc, 'sqlite_errorcode', 0) & 255
        reason = ('database_corrupt' if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB) else
                  'busy' if code in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED) else
                  'open_failed' if stage == 'open' else 'io')
        result = _observation(reason, stage, steps=ledger.consumed, digest=digest)
    except (Exception, KeyboardInterrupt):
        result = _observation('unattached_unreadable' if conn is None else 'identity_changed',
                              stage, steps=ledger.consumed, digest=digest)
    finally:
        if conn is not None:
            try:
                conn.close()
            except (Exception, KeyboardInterrupt):
                result = _observation('io', 'close', steps=ledger.consumed, digest=digest)
    if time.monotonic() >= deadline:
        result = _observation('deadline', 'supervision', steps=ledger.consumed, digest=digest)
    return result


def _safe_read(parent: int, name: str, limit: int) -> dict:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(fd, 'rb') as stream:
        before = os.fstat(stream.fileno())
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > limit or stat.S_IMODE(before.st_mode) != 0o600):
            raise ValueError('unsafe_report')
        body = stream.read(limit + 1)
        if len(body) > limit or signature(before) != signature(os.fstat(stream.fileno())):
            raise ValueError('changed_report')
        if signature(before) != signature(os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise ValueError('changed_report')
    result = json.loads(body)
    if type(result) is not dict:
        raise ValueError('invalid_report')
    return result


def latest_report(home: Path, runtime_root: Path, slug: str) -> dict | None:
    """Read only the bounded latest projection; never open an org database.

    Missing means no recorded report. Unsafe/malformed evidence is explicitly
    unavailable. A stale valid report keeps its original date and outcome.
    """
    key = runtime_key(runtime_root)
    unavailable = dict(outcome='unavailable', reason='report_unavailable', checked_at=None,
                       scheduled_date=None, reporting=dict(external='unavailable', audit='unavailable'))
    if not _SLUG.fullmatch(slug):
        return unavailable
    deadline = time.monotonic() + 1
    try:
        with directory(home / 'workflow-integrity' / key / slug, deadline) as parent:
            value = _safe_read(parent, 'latest.json', 32 * 1024)
        date = value['scheduled_date']
        if (value['format'] != FORMAT or value['runtime_key'] != key or value['org'] != slug
                or type(date) is not str or not _DATE.fullmatch(date)
                or datetime.fromisoformat(date).date().isoformat() != date
                or value['report_id'] != hashlib.sha256(_json([key, slug, date])).hexdigest()
                or not _valid_observation({name: value[name] for name in
                    ('outcome', 'reason', 'stage', 'steps', 'snapshot_identity')})):
            raise ValueError('invalid_report')
        checked = value['finished_at']
        if checked is None:
            raise ValueError('unfinished_report')
        if type(checked) is not str or len(checked) > 40:
            raise ValueError('invalid_report_time')
        stamp = datetime.fromisoformat(checked.replace('Z', '+00:00'))
        if stamp.tzinfo is None or stamp.utcoffset() != timezone.utc.utcoffset(stamp):
            raise ValueError('invalid_report_time')
        reporting = value['reporting']
        if (type(reporting) is not dict or set(reporting) != {'external', 'audit'}
                or any(type(item) is not str or item not in ('persisted', 'unavailable', 'pending')
                       for item in reporting.values()) or time.monotonic() >= deadline):
            raise ValueError('invalid_reporting')
        return dict(outcome=value['outcome'], reason=value['reason'], checked_at=checked,
                    scheduled_date=date, reporting=reporting)
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, KeyError, TimeoutError, Expired, RecursionError):
        return unavailable


def _exclusive(parent: int, name: str, value: dict) -> None:
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=parent)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(_json(value))
        stream.flush()
        os.fsync(stream.fileno())
        if os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError('unsafe_report')
        if signature(os.fstat(stream.fileno())) != signature(os.stat(name, dir_fd=parent, follow_symlinks=False)):
            raise ValueError('changed_report')
    os.fsync(parent)


def _report_dir(home: Path, key: str, slug: str, deadline: float) -> Path:
    if not re.fullmatch(r'[0-9a-f]{64}', key) or not _SLUG.fullmatch(slug):
        raise ValueError('invalid_report_identity')
    path = home
    for component in ('workflow-integrity', key, slug):
        with directory(path, deadline) as parent:
            try:
                os.mkdir(component, 0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
        path = path / component
        with directory(path, deadline) as child:
            if stat.S_IMODE(os.fstat(child).st_mode) != 0o700:
                raise ValueError('unsafe_report_directory')
    return path


def _latest(parent: int, report: dict, deadline: float) -> None:
    deadline = min(deadline, time.monotonic() + 1)
    try:
        old = _safe_read(parent, 'latest.json', 32 * 1024)
    except FileNotFoundError:
        old = None
    if time.monotonic() >= deadline:
        raise TimeoutError('latest_unavailable')
    if old is not None and old['scheduled_date'] > report['scheduled_date']:
        return
    # Exclusive pending inode; never reuse ambiguous crash residue or aliases.
    _exclusive(parent, 'latest.pending', report)
    if old is not None:
        current = _safe_read(parent, 'latest.json', 32 * 1024)
        if current != old:
            raise ValueError('changed_latest')
    else:
        try:
            os.stat('latest.json', dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            raise ValueError('changed_latest')
    if time.monotonic() >= deadline:
        raise TimeoutError('latest_unavailable')
    os.replace('latest.pending', 'latest.json', src_dir_fd=parent, dst_dir_fd=parent)
    os.fsync(parent)


def _audit(root: Path, report: dict, deadline: float) -> None:
    """Existing audit writer, on a NEW bounded connection after checker close."""
    import threading
    import runtime.infrastructure.database  # noqa: F401 -- facade for writer decorator
    from runtime.infrastructure.db.audit import AuditMixin

    class Writer(AuditMixin):
        def __init__(self, conn: sqlite3.Connection) -> None:
            self._conn = conn
            self._lock = threading.RLock()

    before = _db_identity(root, deadline)
    if report['snapshot_identity'] is not None and hashlib.sha256(_json(before)).hexdigest() != report['snapshot_identity']:
        raise ValueError('identity_changed')
    conn = sqlite3.connect((root / 'happyranch.db').as_uri() + '?mode=rw', uri=True,
                           timeout=max(0, min(.5, deadline - time.monotonic())))
    try:
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        if _db_identity(root, deadline) != before:
            raise ValueError('identity_changed')
        conn.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        Writer(conn).insert_audit_log('', 'daemon', ACTION, report)
    finally:
        conn.close()


def _storage(request: dict, deadline: float) -> dict:
    report = request['report']
    _json(report, REPORT_LIMIT)
    date = report['scheduled_date']
    if not _DATE.fullmatch(date):
        raise ValueError('invalid_date')
    home, root = Path(request['home']), Path(request['root'])
    published = False
    audit = 'unavailable'
    path = None
    try:
        path = _report_dir(home, report['runtime_key'], report['org'], deadline)
        with directory(path, deadline) as parent:
            if request['operation'] == 'reserve':
                try:
                    _exclusive(parent, date + '.reserved.json', report)
                except FileExistsError:
                    return dict(reserved=False)
                return dict(reserved=True)
            receipt = _safe_read(parent, date + '.reserved.json', PIPE_LIMIT)
            if receipt['report_id'] != report['report_id']:
                raise ValueError('receipt_changed')
            _exclusive(parent, date + '.json', report)
            published = True
    except Exception:
        if request['operation'] == 'reserve':
            return dict(reserved=False, unavailable=True)
    # Date observation stays immutable. Latest carries separately observed audit
    # reporting status, including failure, without rewriting the check outcome.
    audit_report = dict(report, reporting=dict(external='persisted' if published else 'unavailable', audit='pending'))
    try:
        _audit(root, audit_report, deadline)
        audit = 'persisted'
    except Exception:
        pass
    latest = dict(report, reporting=dict(external='persisted' if published else 'unavailable', audit=audit))
    if published and path is not None:
        try:
            with directory(path, deadline) as parent:
                _latest(parent, latest, deadline)
        except Exception:
            return dict(external='persisted', audit=audit, latest='unavailable')
    return dict(external='persisted' if published else 'unavailable', audit=audit,
                latest='persisted' if published else 'unavailable')


@dataclass(eq=False)
class _Invocation:
    spawn: asyncio.Task
    kind: str
    process: Any = None
    exchange: asyncio.Task | None = None
    stopped: bool = False
    expired: bool = False


class OrgIntegrityMonitor:
    """Lifespan-owned tasks and unreaped ORIGINAL subprocess handles."""

    def __init__(self, state: Any) -> None:
        self.state = state
        self.tasks: set[asyncio.Task] = set()
        self.in_flight: set[_Invocation] = set()
        self.attempted: set[tuple[str, str, str]] = set()
        self.closed = False

    def launch(self, due: datetime) -> None:
        if self.closed:
            return
        task = asyncio.create_task(self.run_pass(due))
        self.tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task) -> None:
        self.tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            LOG.warning('workflow_integrity_report_unavailable reason=report_unavailable')

    @staticmethod
    def _signal(invocation: _Invocation, sig: int) -> None:
        if invocation.process is None and invocation.spawn.done() and not invocation.spawn.cancelled():
            try:
                invocation.process = invocation.spawn.result()
            except Exception:
                invocation.stopped = True
        if invocation.process is not None:
            try:
                os.killpg(invocation.process.pid, sig)
            except ProcessLookupError:
                pass

    async def _stop(self, invocation: _Invocation, cleanup_deadline: float) -> None:
        self._signal(invocation, signal.SIGTERM)
        if invocation.exchange is not None:
            await asyncio.wait({invocation.exchange}, timeout=max(0, min(.1, cleanup_deadline - time.monotonic())))
        self._signal(invocation, signal.SIGKILL)
        if invocation.exchange is not None:
            await asyncio.wait({invocation.exchange}, timeout=max(0, cleanup_deadline - time.monotonic()))
        # returncode is set only after the subprocess transport observed exit.
        invocation.stopped = invocation.stopped or (invocation.process is not None and invocation.process.returncode is not None)
        if invocation.stopped:
            self.in_flight.discard(invocation)

    async def invoke(self, kind: str, request: dict, deadline: float) -> dict | None:
        request = dict(request, deadline=deadline)
        body = _json(request)
        spawn = asyncio.create_task(asyncio.create_subprocess_exec(
            *helper_argv(kind), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=subprocess.DEVNULL,
            start_new_session=True, limit=PIPE_LIMIT + 1))
        invocation = _Invocation(spawn, kind)
        self.in_flight.add(invocation)

        async def exchange() -> dict:
            process = invocation.process = await asyncio.shield(spawn)
            process.stdin.write(body)
            await process.stdin.drain()
            process.stdin.close()
            output = await process.stdout.read(PIPE_LIMIT + 1)
            if len(output) > PIPE_LIMIT:
                raise ValueError('oversized_worker_record')
            # read(n) may be short before EOF. Consume the remainder boundedly.
            tail = await process.stdout.read(PIPE_LIMIT + 1 - len(output))
            output += tail
            if len(output) > PIPE_LIMIT or await process.stdout.read(1):
                raise ValueError('oversized_worker_record')
            await process.wait()
            if process.returncode != 0 or time.monotonic() >= deadline:
                raise ValueError('unavailable_worker')
            value = json.loads(output)
            if type(value) is not dict:
                raise ValueError('invalid_worker_record')
            return value

        invocation.exchange = asyncio.create_task(exchange())
        # Consume late exceptions without losing the retained process handle.
        invocation.exchange.add_done_callback(lambda task: None if task.cancelled() else task.exception())
        cancelled = False
        try:
            done, _ = await asyncio.wait({invocation.exchange}, timeout=max(0, deadline - time.monotonic()))
            if done and time.monotonic() < deadline:
                result = invocation.exchange.result()
                invocation.stopped = True
                self.in_flight.discard(invocation)
                return result
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            pass
        # Spawn may itself still be in flight. On its eventual return, kill
        # that original child; do not replace it or drop ownership on timeout.
        invocation.expired = True
        spawn.add_done_callback(lambda _: self._signal(invocation, signal.SIGKILL))
        await self._stop(invocation, min(deadline + CLEANUP_SECONDS,
                                         time.monotonic() + CLEANUP_SECONDS))
        if cancelled:
            raise asyncio.CancelledError
        return None

    def _reap_observed(self) -> None:
        for invocation in tuple(self.in_flight):
            if invocation.expired:
                self._signal(invocation, signal.SIGKILL)
            if invocation.stopped or (invocation.process is not None and invocation.process.returncode is not None):
                self.in_flight.discard(invocation)

    async def run_pass(self, due: datetime) -> None:
        from runtime.runtime import daemon_home
        runtime = self.state.runtime
        if runtime is None:
            return
        home, key = daemon_home(), runtime_key(runtime.root)
        known = sorted(set(self.state.orgs_snapshot()) | set(self.state.broken_orgs))
        inventory = await self.invoke('inventory', dict(root=str(runtime.root), known=known), time.monotonic() + 2)
        if inventory is None:
            LOG.warning('workflow_integrity_report_unavailable reason=unattached_unreadable')
            return
        for slug in inventory['orgs']:
            if not _SLUG.fullmatch(slug):
                continue
            date = due.date().isoformat()
            if (key, slug, date) in self.attempted:
                continue
            self.attempted.add((key, slug, date))
            report = dict(format=FORMAT, runtime_key=key, org=slug,
                          scheduled_date=date, timezone=due.tzname(), timezone_offset=due.strftime('%z'),
                          started_at=_utc(), finished_at=None, elapsed_seconds=0.0,
                          report_id=hashlib.sha256(_json([key, slug, date])).hexdigest(),
                          **_observation('interrupted', 'receipt'),
                          severity='warning',
                          reporting=dict(external='pending', audit='pending'))
            root = runtime.root / 'orgs' / slug
            request = dict(home=str(home), root=str(root), report=report)
            reserved = await self.invoke('storage', dict(request, operation='reserve'), time.monotonic() + 2)
            if not reserved or not reserved.get('reserved'):
                if reserved is None or reserved.get('unavailable'):
                    LOG.warning('workflow_integrity_report_unavailable reason=report_unavailable')
                    report.update(_observation('report_unavailable', 'receipt'), finished_at=_utc())
                    await self.invoke('storage', dict(request, report=report, operation='publish'), time.monotonic() + 2)
                continue
            self._reap_observed()
            started = time.monotonic()
            cancelled = False
            if any(invocation.kind == 'read' for invocation in self.in_flight):
                checked = _observation('in_flight_reader', 'supervision')
            else:
                try:
                    checked = await self.invoke('read', dict(root=str(root)), started + READ_SECONDS)
                except asyncio.CancelledError:
                    checked, cancelled = _observation('interrupted', 'supervision'), True
                if checked is None:
                    checked = _observation('deadline' if time.monotonic() >= started + READ_SECONDS else 'io', 'supervision')
                elif time.monotonic() >= started + READ_SECONDS:
                    checked = _observation('deadline', 'supervision')
            if not _valid_observation(checked):
                checked = _observation('io', 'supervision')
            report.update(checked, finished_at=_utc(), elapsed_seconds=max(0, time.monotonic() - started))
            report['severity'] = {'ok': 'info', 'failed': 'error', 'unavailable': 'warning'}[report['outcome']]
            request['report'] = report
            receipt = await self.invoke('storage', dict(request, operation='publish'), time.monotonic() + 2)
            if receipt is None or all(receipt.get(sink) != 'persisted' for sink in ('external', 'audit')):
                LOG.warning('workflow_integrity_report_unavailable reason=report_unavailable')
            else:
                LOG.info('workflow_integrity_check org=%s date=%s outcome=%s reason=%s reporting=%s',
                         slug, date, report['outcome'], report['reason'], receipt)
            if cancelled:
                raise asyncio.CancelledError

    async def close(self) -> None:
        self.closed = True
        for task in self.tasks:
            task.cancel()
        # A single shutdown reserve, never an unbounded join of stuck readers.
        until = time.monotonic() + CLEANUP_SECONDS
        if self.tasks:
            await asyncio.wait(self.tasks, timeout=max(0, until - time.monotonic()))
        for invocation in tuple(self.in_flight):
            await self._stop(invocation, until)
        if self.in_flight:
            LOG.warning('workflow_integrity_report_unavailable reason=in_flight_reader')


def _valid_observation(value: Any) -> bool:
    if type(value) is not dict or set(value) != {'outcome', 'reason', 'stage', 'steps', 'snapshot_identity'}:
        return False
    digest = value['snapshot_identity']
    return (type(value['reason']) is str and type(value['stage']) is str
            and value['reason'] in _REASONS and value['stage'] in _STAGES
            and value['outcome'] == _observation(value['reason'], value['stage'])['outcome']
            and type(value['steps']) is int and 0 <= value['steps'] <= 640_000_000
            and (digest is None or type(digest) is str and re.fullmatch(r'[a-f0-9]{64}', digest) is not None))


def helper_main(argv: list[str]) -> int:
    """Fixed pre-start dispatch. No daemon, listener, OrgState or initializer."""
    try:
        if len(argv) != 1 or argv[0] not in ('read', 'storage', 'inventory'):
            return 2
        body = sys.stdin.buffer.read(PIPE_LIMIT + 1)
        if len(body) > PIPE_LIMIT:
            return 2
        request = json.loads(body)
        deadline = request['deadline']
        if type(deadline) not in (int, float) or not math.isfinite(deadline) or time.monotonic() >= deadline:
            return 1
        if argv[0] == 'read':
            result = read_snapshot(Path(request['root']), min(deadline, time.monotonic() + READ_SECONDS))
        elif argv[0] == 'storage':
            result = _storage(request, deadline)
        else:
            from runtime.runtime import RuntimeDir
            roots = {slug for slug, _ in RuntimeDir(Path(request['root'])).iter_org_roots()}
            result = dict(orgs=sorted(roots | set(request['known'])))
        sys.stdout.buffer.write(_json(result))
        sys.stdout.buffer.flush()
        return 0
    except (Exception, KeyboardInterrupt):
        return 1
