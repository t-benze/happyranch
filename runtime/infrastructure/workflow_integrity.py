"""Explicit full physical checks, independent of daemon and logical readers.

The caller owns the connection's progress slot. Checks perform no writes and
work on read-only/query-only connections. A ledger belongs to one operation;
sharing it never grants an ownership, inventory or complete-reader allowance.
"""
from __future__ import annotations

import math
import sqlite3
import time
from dataclasses import dataclass
from typing import Literal


@dataclass
class IntegrityLedger:
    """Cumulative VM reservations, including a conservatively charged tail."""

    deadline: float
    sql_limit: int = 640_000_000
    consumed: int = 0

    def __post_init__(self) -> None:
        if (type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
                or type(self.sql_limit) is not int or not 0 < self.sql_limit <= 640_000_000
                or type(self.consumed) is not int or not 0 <= self.consumed <= self.sql_limit):
            raise ValueError('integrity_ledger_invalid')


@dataclass(frozen=True)
class IntegrityResult:
    outcome: Literal['ok', 'failed', 'unavailable']
    cause: str


class InstallIntegrityError(ValueError):
    """Only closed codes cross the installer output boundary."""

    def __init__(self, stage: str, result: IntegrityResult) -> None:
        self.stage = stage
        self.result = result
        code = 'install_integrity_failed' if result.outcome == 'failed' else 'install_integrity_unavailable'
        detail = '' if result.outcome == 'failed' else ' cause=' + result.cause
        super().__init__(code + ' stage=' + stage + detail)


def check_full_integrity(conn: sqlite3.Connection, ledger: IntegrityLedger) -> IntegrityResult:
    """Native full PRAGMA; only one literal ok row and normal EOF can pass.

    Reserve before SQLite runs, then reserve the next quantum at each native
    progress callback. The unused final quantum stays debited across checks.
    Thus even short statements and interrupted checks cannot replenish steps.
    No SQLite error text, SQL or returned cells leave this helper.
    """
    unavailable = lambda cause: IntegrityResult('unavailable', cause)
    if time.monotonic() >= ledger.deadline:
        return unavailable('deadline')
    remaining = ledger.sql_limit - ledger.consumed
    if remaining <= 0:
        return unavailable('sql_budget')
    quantum = min(1000, remaining)
    stopped = None

    def progress() -> int:
        nonlocal stopped
        if time.monotonic() >= ledger.deadline:
            stopped = 'deadline'
        elif ledger.consumed + quantum > ledger.sql_limit:
            stopped = 'sql_budget'
        else:
            ledger.consumed += quantum
            return 0
        return 1

    cursor = None
    result = unavailable('invalid_result')
    ledger.consumed += quantum
    try:
        conn.set_progress_handler(progress, quantum)
        cursor = conn.cursor()
        cursor.row_factory = None
        cursor.execute('PRAGMA integrity_check')
        first = cursor.fetchone()
        if type(first) is tuple and len(first) == 1 and type(first[0]) is str:
            if first[0] != 'ok':
                result = IntegrityResult('failed', 'full_integrity_not_ok')
            elif cursor.fetchone() is None:
                result = IntegrityResult('ok', 'full_integrity_ok')
    except sqlite3.Error as exc:
        code = getattr(exc, 'sqlite_errorcode', None)
        primary = code & 255 if type(code) is int else None
        if stopped is not None:
            result = unavailable(stopped)
        elif primary in (sqlite3.SQLITE_CORRUPT, sqlite3.SQLITE_NOTADB):
            result = IntegrityResult('failed', 'database_corrupt')
        elif primary in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            result = unavailable('busy')
        elif primary in (sqlite3.SQLITE_IOERR, sqlite3.SQLITE_CANTOPEN,
                         sqlite3.SQLITE_READONLY, sqlite3.SQLITE_FULL, sqlite3.SQLITE_NOMEM):
            result = unavailable('io')
        else:
            result = unavailable('invalid_result')
    except (Exception, KeyboardInterrupt):
        result = unavailable('invalid_result')
    finally:
        try:
            if cursor is not None:
                cursor.close()
        except (Exception, KeyboardInterrupt):
            result = unavailable('invalid_result')
        try:
            conn.set_progress_handler(None, 0)
        except (Exception, KeyboardInterrupt):
            result = unavailable('invalid_result')
    if stopped is not None:
        return unavailable(stopped)
    if time.monotonic() >= ledger.deadline:
        return unavailable('deadline')
    if ledger.consumed >= ledger.sql_limit:
        return unavailable('sql_budget')
    return result


def require_install_integrity(conn: sqlite3.Connection, ledger: IntegrityLedger,
                              stage: Literal['source', 'backup', 'writer_source', 'target']) -> None:
    result = check_full_integrity(conn, ledger)
    if result.outcome != 'ok':
        raise InstallIntegrityError(stage, result)
