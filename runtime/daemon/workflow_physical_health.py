"""Per-attachment physical observation; never a semantic or authority cache.

Registry and lifecycle publication belong to the event loop. Native code owns
only its isolated read snapshot/result and the short synchronized incident latch.
The deadline is cooperative, including executor queue and cleanup time.
"""
from __future__ import annotations

import asyncio
import logging
import sqlite3
import threading
import time
from concurrent.futures import BrokenExecutor, Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

logger = logging.getLogger(__name__)
_INTERVAL = 3600.0
_ALLOWANCE = 60.0
_STALE = 14400.0


def _file_identity(path: Path) -> tuple[int, int]:
    observed = path.stat()
    return observed.st_dev, observed.st_ino


@dataclass(eq=False)
class _Attempt:
    deadline: float
    incident: int
    identity: tuple[int, int]
    stop: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None
    task: asyncio.Task | None = None
    category: str = 'checking'
    secondary: str | None = None
    stop_reason: str | None = None
    started: str | None = None
    finished: float | None = None
    finished_at: str | None = None
    rollback_ok: bool = False
    close_ok: bool = False


class WorkflowPhysicalHealth:
    def __init__(self, org: Any, *, startup: tuple[float, str], identity: tuple[int, int]) -> None:
        self.org, self.db = org, org.db
        self.path = self.db.db_path
        self.boot = org.authority_v2_origin_boot_id
        self.identity = identity
        self._daemon = None
        self._mutex = threading.Lock()
        self._current = True
        self._closing = False
        self._incident = 0
        self._corrupt = False
        self._last_success, self._last_success_at = startup
        self._category = 'startup_full'
        self._secondary = None
        self._scheduling: str | None = None
        self._last_started: str | None = None
        self._last_finished_at: str | None = None
        self._next_due = startup[0] + _INTERVAL
        self._attempt: _Attempt | None = None
        self._scheduler: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='workflow-physical')

    def attach(self, daemon: Any) -> None:
        # Real map insertion precedes binding. No slug-based evidence transfer.
        if daemon.orgs.get(self.org.slug) is not self.org or self.db is not self.org.db:
            raise ValueError('workflow_physical_attachment_changed')
        if (_file_identity(self.path) != self.identity
                or self.org.authority_v2_origin_boot_id != self.boot):
            raise ValueError('workflow_physical_attachment_changed')
        self._daemon = daemon
        self.db._workflow_physical = self

    def _identity_current(self) -> bool:
        daemon = self._daemon
        if (daemon is None or daemon.orgs.get(self.org.slug) is not self.org
                or self.org.db is not self.db or self.db.db_path != self.path
                or self.org.authority_v2_origin_boot_id != self.boot):
            return False
        try:
            return _file_identity(self.path) == self.identity
        except OSError:
            return False

    def matches(self, db: Any, conn: sqlite3.Connection, slug: str | None) -> bool:
        # File I/O and map inspection are outside the physical mutex.
        if db is not self.db or conn is not db._conn or slug != self.org.slug or not self._identity_current():
            return False
        with self._mutex:
            return self._current

    def reserve(self, db: Any, conn: sqlite3.Connection) -> bool:
        """Caller owns its original DB/reservation exclusion and all validations.

        Linearization against corruption is this short section. Its Boolean is
        consumed by the immediate owned synchronous effect; never retain it.
        """
        current = self.matches(db, conn, self.org.slug)
        with self._mutex:
            age = time.monotonic() - self._last_success
            return current and self._current and not self._closing and not self._corrupt and 0 <= age < _STALE

    def status(self) -> dict:
        current = self._identity_current()
        now = time.monotonic()
        with self._mutex:
            age = now - self._last_success
            hold = ('attachment_unavailable' if not current or not self._current or self._closing else
                    'corrupt' if self._corrupt else 'clock_invalid' if age < 0 else
                    'stale' if age >= _STALE else None)
            attempt = self._attempt
            overdue = attempt is not None and now >= attempt.deadline
            category = self._category
            if attempt is not None and category == 'checking':
                category = (attempt.category if attempt.category not in ('checking', 'ok')
                            else 'timeout' if overdue else category)
            alert = category not in ('startup_full', 'ok', 'checking') or self._scheduling is not None or now >= self._next_due or overdue
            return dict(status='physical-held' if hold else 'alert' if alert else 'checking' if attempt else 'healthy',
                age_seconds=max(0.0, age), category=category, hold=hold,
                last_success=self._last_success_at, in_flight_cleanup=attempt is not None,
                snapshot_started=attempt.started if attempt else self._last_started,
                completed_at=self._last_finished_at, next_due_in_seconds=max(0.0, self._next_due - now),
                secondary_category=(attempt.secondary if attempt is not None and attempt.secondary
                                    else self._secondary), scheduling_category=self._scheduling)

    def _latch_corruption(self, attempt: _Attempt) -> None:
        # Worker never acquires DB/org/state locks or touches loop-owned objects.
        with self._mutex:
            if self._current and self._attempt is attempt:
                self._incident += 1
                self._corrupt = True
                self._category = "corrupt"

    def _scan(self, attempt: _Attempt) -> _Attempt:
        conn = None
        begun = False
        def interrupted() -> bool:
            return attempt.stop.is_set() or time.monotonic() >= attempt.deadline
        def remaining() -> float:
            if interrupted():
                raise TimeoutError('workflow_physical_deadline')
            return attempt.deadline - time.monotonic()
        def execute(sql: str) -> sqlite3.Cursor:
            # Every SQLite admission uses the SAME remaining allowance.
            conn.execute("PRAGMA busy_timeout=" + str(max(0, int(remaining() * 1000))))
            return conn.execute(sql)
        try:
            remaining()
            if _file_identity(self.path) != attempt.identity:
                raise ValueError('attachment_changed')
            conn = sqlite3.connect('file:' + quote(str(self.path), safe='/') + '?mode=ro',
                                   uri=True, timeout=max(0.0, remaining()))
            conn.set_progress_handler(lambda: int(interrupted()), 1000)
            execute('PRAGMA query_only=ON')
            execute('BEGIN')
            begun = True
            # Acquire the real snapshot before the second path observation.
            execute('SELECT name FROM sqlite_schema LIMIT 1').fetchone()
            remaining()
            if _file_identity(self.path) != attempt.identity:
                raise ValueError('attachment_changed')
            attempt.started = datetime.now(timezone.utc).isoformat()
            count = 0
            for row in execute('PRAGMA integrity_check'):
                count += 1
                if row != ('ok',) or count != 1:
                    attempt.category = 'corrupt'
                    self._latch_corruption(attempt)
                    break
                remaining()
            else:
                if count != 1:
                    attempt.category = 'corrupt'
                    self._latch_corruption(attempt)
                else:
                    attempt.category = 'ok'
            remaining()
            if _file_identity(self.path) != attempt.identity:
                raise ValueError('attachment_changed')
        except sqlite3.DatabaseError as exc:
            code = getattr(exc, 'sqlite_errorcode', 0) & 255
            if code in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
                attempt.category = 'corrupt'
                self._latch_corruption(attempt)
            elif attempt.category != 'corrupt':
                attempt.category = (attempt.stop_reason or 'timeout') if interrupted() else 'sqlite_unavailable'
        except BaseException as exc:
            if attempt.category != 'corrupt':
                attempt.category = (attempt.stop_reason or 'timeout' if interrupted()
                                    or isinstance(exc, TimeoutError) else 'unavailable')
        finally:
            # Cleanup is mandatory even after expiry; it cannot replenish time.
            # Only positive rollback and close admit loop-owned retirement.
            if conn is None:
                attempt.rollback_ok = attempt.close_ok = True
            else:
                try:
                    conn.set_progress_handler(None, 0)
                    if begun:
                        conn.rollback()
                    attempt.rollback_ok = True
                except BaseException:
                    attempt.secondary = 'rollback_failed'
                try:
                    conn.close()
                    attempt.close_ok = True
                except BaseException:
                    attempt.secondary = ('close_failed' if attempt.secondary is None
                                         else attempt.secondary + '+close_failed')
            attempt.finished = time.monotonic()
            attempt.finished_at = datetime.now(timezone.utc).isoformat()
        return attempt

    def start_scheduler(self) -> None:
        if self._scheduler is None and not self._closing:
            self._wake = asyncio.Event()
            self._scheduler = asyncio.create_task(self._periodic())

    async def _periodic(self) -> None:
        while not self._closing:
            delay = max(0.0, self._next_due - time.monotonic())
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
                return
            except asyncio.TimeoutError:
                pass
            if self._closing:
                return
            # No overlap, catch-up or retry. Each attempt owns its original time.
            now = time.monotonic()
            scheduled = self._next_due
            self._next_due += (int(max(0.0, now - self._next_due) // _INTERVAL) + 1) * _INTERVAL
            if self._attempt is not None:
                with self._mutex:
                    self._scheduling = 'missed'
                logger.warning('org %s physical check missed: native cleanup still owned', self.org.slug)
                continue
            deadline = scheduled + _ALLOWANCE
            if now >= deadline:
                with self._mutex:
                    self._scheduling = 'missed'
                logger.warning('org %s physical check missed: original slot allowance expired', self.org.slug)
                continue
            self._begin(deadline)

    def _begin(self, deadline: float) -> None:
        if self._closing or not self._identity_current():
            with self._mutex:
                self._category = 'attachment_unavailable'
            logger.warning('org %s physical check unavailable: attachment changed', self.org.slug)
            return
        with self._mutex:
            attempt = _Attempt(deadline, self._incident, self.identity)
        # Register an actual strong owner BEFORE executor submission or await.
        if not self.org._acquire_native_task(attempt):
            with self._mutex:
                self._category = 'admission_unavailable'
            logger.warning('org %s physical check unavailable: native admission closed', self.org.slug)
            return
        with self._mutex:
            self._attempt = attempt
            self._category = 'checking'
            self._secondary = None
            self._scheduling = None
        try:
            attempt.future = self._executor.submit(self._scan, attempt)
        except BrokenExecutor:
            # ThreadPoolExecutor checks its broken state before queue insertion.
            # Other failures (including worker-start RuntimeError) are ambiguous.
            self.org._release_native_task(attempt)
            with self._mutex:
                self._attempt = None
                self._category = 'submit_rejected'
            logger.warning('org %s physical submission rejected before queue insertion', self.org.slug)
            return
        except BaseException:
            # Delivery is ambiguous: preserve the owner and unsafe-close fence.
            with self._mutex:
                attempt.category = self._category = 'submission_unknown'
            logger.exception('org %s physical submission uncertain', self.org.slug)
            return
        attempt.task = asyncio.create_task(self._finish(attempt))

    async def _finish(self, attempt: _Attempt) -> None:
        actual = asyncio.wrap_future(attempt.future)
        cancellation = None
        expired = False
        while True:
            try:
                if expired or cancellation is not None:
                    result = await asyncio.shield(actual)
                else:
                    # Deadline observation does not cancel the native future.
                    result = await asyncio.wait_for(asyncio.shield(actual),
                        timeout=max(0.0, attempt.deadline - time.monotonic()))
                break
            except asyncio.TimeoutError:
                expired = True
                self._request_stop(attempt, 'timeout')
                with self._mutex:
                    if not self._corrupt:
                        self._category = (attempt.category if attempt.category not in ('checking', 'ok')
                                          else attempt.stop_reason)
                logger.warning('org %s physical check timed out; native cleanup remains owned', self.org.slug)
            except asyncio.CancelledError as exc:
                if actual.cancelled():
                    with self._mutex:
                        self._completion_unknown(attempt)
                    logger.error('org %s physical future cancelled without cleanup proof', self.org.slug)
                    return
                cancellation = cancellation or exc
                self._request_stop(attempt, 'cancelled')
            except BaseException:
                with self._mutex:
                    self._completion_unknown(attempt)
                logger.exception('org %s physical native completion unavailable', self.org.slug)
                return
        current = self._identity_current()
        clean = result is attempt and attempt.rollback_ok and attempt.close_ok
        with self._mutex:
            if self._attempt is not attempt:
                return
            primary = attempt.stop_reason if attempt.category in ('checking', 'ok', 'cancelled') else None
            self._category = primary or ('cleanup_failed' if not clean and attempt.category == 'ok' else attempt.category)
            self._secondary = attempt.secondary
            self._last_started = attempt.started
            self._last_finished_at = attempt.finished_at
            timely = (attempt.finished is not None and attempt.finished <= attempt.deadline
                      and time.monotonic() <= attempt.deadline and not expired and cancellation is None
                      and not attempt.stop.is_set())
            if (clean and timely and current and self._current and not self._closing
                    and self._incident == attempt.incident and attempt.category == 'ok'):
                self._last_success = attempt.finished
                self._last_success_at = attempt.finished_at
                self._corrupt = False
            elif clean and attempt.category == 'ok' and primary is None:
                self._category = 'late' if not timely else 'attachment_unavailable'
        if clean:
            # Actual native return plus positive cleanup. Registry is loop-only.
            self.org._release_native_task(attempt)
            with self._mutex:
                self._attempt = None
        else:
            logger.error('org %s physical cleanup uncertain: %s/%s', self.org.slug, attempt.category, attempt.secondary)
        if self._category != 'ok':
            logger.warning('org %s physical observation: %s', self.org.slug, self._category)

    def _request_stop(self, attempt: _Attempt, reason: str) -> None:
        with self._mutex:
            if attempt.stop_reason is None:
                attempt.stop_reason = 'timeout' if time.monotonic() >= attempt.deadline else reason
        attempt.stop.set()

    def _completion_unknown(self, attempt: _Attempt) -> None:
        # Caller holds only the short physical mutex; no cleanup is inferred.
        primary = (attempt.category if attempt.category not in ('checking', 'ok')
                   else attempt.stop_reason or 'completion_unknown')
        attempt.category = self._category = primary
        if primary != 'completion_unknown':
            self._secondary = 'completion_unknown'

    def fence(self) -> None:
        # Called on loop before shutdown's first await; not native retirement.
        with self._mutex:
            self._closing = True
            attempt = self._attempt
        if attempt is not None:
            self._request_stop(attempt, 'cancelled')
        if self._wake is not None:
            self._wake.set()

    async def drain(self, *, _retire: bool = True) -> None:
        self.fence()
        scheduler = self._scheduler
        if scheduler is not None:
            while not scheduler.done():
                try:
                    await asyncio.shield(scheduler)
                except asyncio.CancelledError:
                    continue
            if not scheduler.cancelled():
                scheduler.result()
        attempt = self._attempt
        if attempt is not None and attempt.future is not None:
            while True:
                task = attempt.task
                # Cancellation before the observer's first instruction cannot
                # cancel native ownership. Rejoin only the SAME actual future.
                if task is None or task.cancelled():
                    task = attempt.task = asyncio.create_task(self._finish(attempt))
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                if task.cancelled():
                    continue
                task.result()
                break
        self.assert_idle()
        if _retire:
            self._executor.shutdown(wait=False, cancel_futures=False)

    def _resume_retained_attachment(self) -> None:
        # Only a completed nonretiring drain of this SAME current attachment.
        # No native owner, incident, last-success or unsafe barrier is reset.
        if not self._identity_current():
            return
        with self._mutex:
            if not self._current or self._attempt is not None:
                return
            self._closing = False
        self._scheduler = None
        self.start_scheduler()

    def assert_idle(self) -> None:
        if self._attempt is not None:
            from runtime.daemon.org_state import _OrgBusyError
            raise _OrgBusyError('workflow_physical_native_in_flight_or_unsafe')

    def invalidate(self) -> None:
        self.assert_idle()
        self.fence()
        with self._mutex:
            self._current = False
            self._closing = True
        self._executor.shutdown(wait=False, cancel_futures=False)
