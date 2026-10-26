"""Finite thread reads on worker-owned read-only SQLite snapshots.

This is a separate native domain from recovery and the HTTP/default executor.
The only database methods it exposes are the ten selected canonical readers;
it neither constructs Database nor inherits any writer mixin.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator

from runtime.daemon.startup_recovery import StartupRecovery
from runtime.infrastructure.db.reply_delivery import ReplyDeliveryMixin
from runtime.infrastructure.db.threads import ThreadsMixin

_ACTION = "Retry a finite paged read; if this persists, send this code to engineering_manager."
_MAX_RAW = 4 * 1024 * 1024
_MAX_VALUE = 1024 * 1024


class StartupReadUnavailable(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code

    def detail(self, startup: dict[str, Any]) -> dict[str, Any]:
        action = _ACTION
        if self.code == "startup_reader_unbounded_request":
            action = ("Use page_size=50 for the inbox, or a nonnegative limit<=1000 for messages; "
                      "retry legacy unbounded reads after recovery is ready.")
        return {"code": self.code, "startup": startup, "required_action": action}


@dataclass
class _ReadBudget:
    deadline: float
    cancelled: threading.Event = field(default_factory=threading.Event)
    rows: int = 0
    raw_bytes: int = 0
    response_bytes: int = 0
    opcodes: int = 0
    attachments: int = 0
    refusal: StartupReadUnavailable | None = None

    def refuse(self, code: str) -> None:
        if self.refusal is None:
            self.refusal = StartupReadUnavailable(code)
        raise self.refusal

    def check(self) -> None:
        if self.refusal is not None:
            raise self.refusal
        if self.cancelled.is_set():
            self.refuse("startup_reader_unavailable")
        if time.monotonic() >= self.deadline:
            self.refuse("startup_reader_deadline")

    def progress(self) -> int:
        self.opcodes += 1000
        try:
            self.check()
            if self.opcodes > 2_000_000:
                self.refuse("startup_reader_budget_exhausted")
        except StartupReadUnavailable:
            return 1
        return 0

    def raw(self, row: sqlite3.Row) -> None:
        self.check()
        self.rows += 1
        if self.rows > 4096:
            self.refuse("startup_reader_budget_exhausted")
        for value in row:
            # Bound the value BEFORE creating a second UTF8 representation.
            # SQLite may already have allocated one large raw value here.
            if isinstance(value, (str, bytes)):
                if len(value) > _MAX_VALUE:
                    self.refuse("startup_reader_budget_exhausted")
                size = len(value.encode("utf-8")) if isinstance(value, str) else len(value)
                if size > _MAX_VALUE:
                    self.refuse("startup_reader_budget_exhausted")
            else:
                size = 8 if value is not None else 0
            self.raw_bytes += size
            if self.raw_bytes > _MAX_RAW:
                self.refuse("startup_reader_budget_exhausted")
        self.check()

    def attachment(self) -> None:
        self.attachments += 1
        if self.attachments > 128:
            self.refuse("startup_reader_budget_exhausted")
        self.check()

    def encode(self, value: Any) -> bytes:
        chunks: list[bytes] = []
        for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False, separators=(",", ":")).iterencode(value):
            self.check()
            encoded = chunk.encode("utf-8")
            self.response_bytes += len(encoded)
            if self.response_bytes > _MAX_RAW:
                self.refuse("startup_reader_budget_exhausted")
            chunks.append(encoded)
        result = b"".join(chunks)
        self.check()
        return result


class _CheckedRow:
    """Keep canonical SQLite row semantics with checks between decoder inputs."""

    def __init__(self, row: sqlite3.Row, budget: _ReadBudget) -> None:
        self._row = row
        self._budget = budget

    def keys(self) -> list[str]:
        self._budget.check()
        return self._row.keys()

    def __getitem__(self, key: str | int) -> Any:
        self._budget.check()
        return self._row[key]

    def __iter__(self) -> Iterator[Any]:
        for value in self._row:
            self._budget.check()
            yield value


class _Cursor:
    def __init__(self, cursor: sqlite3.Cursor, budget: _ReadBudget) -> None:
        self._cursor = cursor
        self._budget = budget

    def fetchone(self) -> _CheckedRow | None:
        self._budget.check()
        row = self._cursor.fetchone()
        if row is not None:
            self._budget.raw(row)
        self._budget.check()
        return None if row is None else _CheckedRow(row, self._budget)

    def fetchall(self) -> list[_CheckedRow]:
        rows: list[_CheckedRow] = []
        while True:
            self._budget.check()
            batch = self._cursor.fetchmany(64)
            if not batch:
                break
            for row in batch:
                self._budget.raw(row)
                rows.append(_CheckedRow(row, self._budget))
        return rows

    def fetchmany(self, size: int = 64) -> list[_CheckedRow]:
        self._budget.check()
        rows = self._cursor.fetchmany(min(size, 64))
        for row in rows:
            self._budget.raw(row)
        self._budget.check()
        return [_CheckedRow(row, self._budget) for row in rows]

    def __iter__(self) -> Iterator[_CheckedRow]:
        while (row := self.fetchone()) is not None:
            yield row


class _Connection:
    def __init__(self, conn: sqlite3.Connection, budget: _ReadBudget) -> None:
        self._conn = conn
        self._budget = budget

    def execute(self, sql: str, parameters: Any = ()) -> _Cursor:
        self._budget.check()
        # Conservatively reserve the unobserved (<1000 instruction) tail of
        # EACH statement. Progress callbacks charge full 1000-unit intervals.
        # This is a work allowance, not an exact VM-instruction measurement.
        self._budget.opcodes += 1000
        if self._budget.opcodes > 2_000_000:
            self._budget.refuse("startup_reader_budget_exhausted")
        return _Cursor(self._conn.execute(sql, parameters), self._budget)


def _canonical(method: Callable[..., Any]) -> Callable[..., Any]:
    # Remove only the Database synchronization decorator: this private
    # connection and every cursor are used exclusively by their native worker.
    original = getattr(method, "__wrapped__", method)

    @wraps(original)
    def read(self: _ThreadReader, *args: Any, **kwargs: Any) -> Any:
        self._budget.check()
        result = original(self, *args, **kwargs)
        self._budget.check()
        return result

    return read


class _ThreadReader:
    get_thread = _canonical(ThreadsMixin.get_thread)
    _row_to_thread = _canonical(ThreadsMixin._row_to_thread)
    list_threads = _canonical(ThreadsMixin.list_threads)
    list_threads_page = _canonical(ThreadsMixin.list_threads_page)
    list_thread_participants = _canonical(ThreadsMixin.list_thread_participants)
    list_thread_participant_names_for_threads = _canonical(ThreadsMixin.list_thread_participant_names_for_threads)
    _attachments_for_messages = _canonical(ThreadsMixin._attachments_for_messages)
    list_thread_messages = _canonical(ThreadsMixin.list_thread_messages)
    list_invocations_for_thread_grouped_by_seq = _canonical(ThreadsMixin.list_invocations_for_thread_grouped_by_seq)
    list_reply_delivery_projections = _canonical(ReplyDeliveryMixin.list_reply_delivery_projections)

    def __init__(self, conn: sqlite3.Connection, budget: _ReadBudget) -> None:
        self._conn = _Connection(conn, budget)
        self._budget = budget
        self._startup_read_budget = budget


@dataclass
class _Lease:
    org: Any
    identity: tuple[Any, ...]
    budget: _ReadBudget
    future: Future[bytes] | None = None
    close_proven: bool = False
    primary_error: BaseException | None = None
    close_error: BaseException | None = None
    started: bool = False
    connection: sqlite3.Connection | None = None


def _identity(org: Any) -> tuple[Any, ...]:
    path = Path(org.db.db_path)
    try:
        current = path.stat()
    except OSError as exc:
        raise StartupReadUnavailable("startup_reader_unavailable") from exc
    return (org, org.root, org.db, path, current.st_dev, current.st_ino,
            org._thread_release_attachment)


class StartupReads:
    def __init__(self, state: Any, recovery: StartupRecovery) -> None:
        self.state = state
        self.recovery = recovery
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="startup-reader")
        self._lock = threading.Lock()
        self._leases: list[_Lease] = []
        self._closed = False
        self._counts = {name: 0 for name in (
            "admitted", "started", "closed", "refused", "cancelled", "failed", "deadline",
            "rows", "raw_bytes", "response_bytes", "opcodes", "attachments")}

    def _validate(self, lease: _Lease) -> None:
        lease.budget.check()
        if (self.state.orgs.get(lease.org.slug) is not lease.org
                or _identity(lease.org) != lease.identity
                or self.recovery.snapshot()["stopping"]):
            lease.budget.refuse("startup_reader_unavailable")
        lease.budget.check()

    async def read(self, org: Any, projection: Callable[[_ThreadReader, _ReadBudget], Any]) -> bytes:
        # Original route auth/OrgDep must have completed before calling this.
        deadline = time.monotonic() + 2.0
        identity = _identity(org)
        with self._lock:
            if self._closed or len(self._leases) >= 4:
                self._counts["refused"] += 1
                raise StartupReadUnavailable("startup_reader_busy")
            snapshot = self.recovery.snapshot()
            if snapshot["stopping"] or snapshot["state"] == "ready":
                raise StartupReadUnavailable("startup_reader_unavailable")
            lease = _Lease(org, identity, _ReadBudget(deadline))
            self._leases.append(lease)
            self._counts["admitted"] += 1
            try:
                lease.future = self._executor.submit(self._read, lease, projection)
            except BaseException:
                self._leases.remove(lease)
                raise
            # The callback owns retirement; request cancellation never does.
        lease.future.add_done_callback(lambda future: self._retire(lease, future))
        try:
            result = await asyncio.shield(asyncio.wrap_future(lease.future))
            self._validate(lease)
            if not lease.close_proven:
                raise StartupReadUnavailable("startup_reader_close_failed")
            return result
        except asyncio.CancelledError:
            lease.budget.cancelled.set()
            with self._lock:
                self._counts["cancelled"] += 1
            lease.future.cancel()  # False for running work; it retains its slot.
            raise

    def _read(self, lease: _Lease, projection: Callable[[_ThreadReader, _ReadBudget], Any]) -> bytes:
        conn: sqlite3.Connection | None = None
        result: bytes | None = None
        with self._lock:
            lease.started = True
            self._counts["started"] += 1
        try:
            self._validate(lease)
            uri = Path(lease.org.db.db_path).absolute().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=0.05)
            lease.connection = conn
            conn.row_factory = sqlite3.Row
            conn.set_progress_handler(lease.budget.progress, 1000)
            lease.budget.opcodes += 5000  # Finite setup/release statement tails.
            conn.execute("PRAGMA query_only=ON")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=50")
            conn.execute("SAVEPOINT startup_read")
            self._validate(lease)
            value = projection(_ThreadReader(conn, lease.budget), lease.budget)
            lease.budget.check()
            result = lease.budget.encode(value)
            conn.execute("RELEASE SAVEPOINT startup_read")
            self._validate(lease)
        except BaseException as exc:
            lease.primary_error = lease.budget.refusal or exc
        finally:
            # Only this worker can close its connection. A failed close remains
            # an owned incomplete lease even after its Future has completed.
            try:
                if conn is not None:
                    conn.close()
                lease.close_proven = True
                lease.connection = None
            except BaseException as exc:
                lease.close_error = exc
        if lease.primary_error is not None:
            if isinstance(lease.primary_error, sqlite3.Error):
                raise StartupReadUnavailable("startup_reader_unavailable") from lease.primary_error
            raise lease.primary_error
        if not lease.close_proven:
            raise StartupReadUnavailable("startup_reader_close_failed") from lease.close_error
        self._validate(lease)
        assert result is not None
        return result

    def _retire(self, lease: _Lease, future: Future[bytes]) -> None:
        # Observe failures even after the HTTP waiter departed. Only native
        # positive close or successful queued cancellation releases admission.
        error = None if future.cancelled() else future.exception()
        with self._lock:
            if error is not None:
                self._counts["failed"] += 1
            if isinstance(error, StartupReadUnavailable) and error.code == "startup_reader_deadline":
                self._counts["deadline"] += 1
            for name in ("rows", "raw_bytes", "response_bytes", "opcodes", "attachments"):
                self._counts[name] += getattr(lease.budget, name)
            if lease.close_proven or (future.cancelled() and not lease.started):
                self._counts["closed"] += int(lease.close_proven)
                self._leases.remove(lease)

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {**self._counts, "active": sum(lease.started for lease in self._leases),
                    "queued": sum(not lease.started for lease in self._leases),
                    "incomplete": sum(lease.future is not None and lease.future.done()
                                      and not lease.close_proven for lease in self._leases)}

    def revoke(self, org: Any | None = None) -> None:
        with self._lock:
            if org is None:
                self._closed = True
            leases = [lease for lease in self._leases if org is None or lease.org is org]
            for lease in leases:
                lease.budget.cancelled.set()
        # cancel() invokes callbacks inline; never call it under the pool lock.
        for lease in leases:
            if lease.future is not None:
                lease.future.cancel()

    async def drain(self, org: Any | None = None) -> None:
        self.revoke(org)
        with self._lock:
            futures = [lease.future for lease in self._leases if org is None or lease.org is org]
        cancellation: asyncio.CancelledError | None = None
        for native in futures:
            if native is None or native.cancelled():
                continue
            wrapped = asyncio.wrap_future(native)
            while True:
                try:
                    await asyncio.shield(wrapped)
                    break
                except asyncio.CancelledError as exc:
                    if native.cancelled():
                        break
                    cancellation = cancellation or exc
                except BaseException:
                    break  # Outcome observed; incomplete close still refuses.
        self.assert_idle(org)
        if org is None:
            self._executor.shutdown(wait=True, cancel_futures=False)
        if cancellation is not None:
            raise cancellation

    def assert_idle(self, org: Any | None = None) -> None:
        with self._lock:
            if any(org is None or lease.org is org for lease in self._leases):
                raise StartupReadUnavailable("startup_reader_close_failed")
