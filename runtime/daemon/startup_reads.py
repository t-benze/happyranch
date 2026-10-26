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

from runtime.infrastructure.startup_observation import observation_time_ns
from runtime.daemon.startup_recovery import StartupRecovery, StartupUnavailable
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
    cleanup_failures: int = 0
    cleanup_primary: BaseException | None = None
    cleanup_refusal: StartupReadUnavailable | None = None

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

    def _startup_read_cleanup(self, savepoint: str, primary: BaseException) -> None:
        # This connection belongs to the read worker. Exhausted work can refuse
        # even rollback/release; preserve the real original error and let the
        # worker's actual close prove disposal of the entire read transaction.
        self._budget.cleanup_primary = primary
        self._budget.cleanup_refusal = self._budget.refusal
        try:
            self._conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
            self._conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        except BaseException as secondary:
            self._budget.cleanup_failures += 1
            primary.add_note("Startup read snapshot cleanup incomplete: " + type(secondary).__name__)


@dataclass
class _Lease:
    org: Any
    references: tuple[Any, ...]
    budget: _ReadBudget
    identity: tuple[int, int] | None = None
    retired: Future[None] = field(default_factory=Future)
    future: Future[bytes] | None = None
    close_proven: bool = False
    primary_error: BaseException | None = None
    close_error: BaseException | None = None
    started: bool = False
    cancel_recorded: bool = False
    connection: sqlite3.Connection | None = None
    submitted_ns: int | None = field(default_factory=observation_time_ns)
    started_ns: int | None = None
    ended_ns: int | None = None


@dataclass(frozen=True)
class _ReadResult:
    content: bytes
    lease: _Lease


def _file_identity(path: Path) -> tuple[int, int]:
    # Called exclusively by the actual admitted native operation.
    try:
        current = path.stat()
    except OSError as exc:
        raise StartupReadUnavailable("startup_reader_unavailable") from exc
    return current.st_dev, current.st_ino


class StartupReads:
    def __init__(self, state: Any, recovery: StartupRecovery) -> None:
        self.state = state
        self.recovery = recovery
        self._app, self._epoch = recovery.app, recovery.epoch
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="startup-reader")
        self._lock = threading.Lock()
        self._leases: list[_Lease] = []
        self._closed = False
        self._counts = {name: 0 for name in (
            "admitted", "started", "closed", "refused", "cancelled", "failed", "deadline",
            "rows", "raw_bytes", "response_bytes", "opcodes", "attachments", "cleanup_failures", "close_failed",
            "retired", "budget_exhausted", "submission_failed", "admissions_max", "observation_failures",
            "queue_ns_total", "queue_ns_max", "native_ns_total", "native_ns_max",
            "rows_max", "raw_bytes_max", "response_bytes_max", "opcodes_max", "attachments_max")}

    def _references(self, org: Any) -> tuple[Any, ...]:
        # Reference-only capture: no stat, DB call or canonical lock on the loop.
        try:
            return (org.slug, org.root, org.db, Path(org.db.db_path),
                    org._thread_release_attachment, org._startup_admission_refs,
                    self.recovery._loop, org._startup_read_file)
        except (AttributeError, TypeError) as exc:
            raise StartupReadUnavailable("startup_reader_unavailable") from exc

    def _validate_references(self, lease: _Lease) -> None:
        lease.budget.check()
        try:
            current = self._references(lease.org)
            original = lease.references
            self.recovery.require_attachment(lease.org)
            valid = (self.state._startup_reads is self and self.state._startup_recovery is self.recovery
                     and lease.org._startup_reads is self and self.recovery.state is self.state
                     and self.recovery._http_references is not None
                     and self.recovery._http_references.reads is self
                     and self.recovery._http_references.state is self.state
                     and self.recovery._http_references.gate is self.recovery
                     and self.recovery._http_references.epoch == self._epoch
                     and self._app.state is self.recovery._http_references.app_state
                     and self._app.state.startup_http is self.recovery._http_references
                     and self.recovery.app is self._app and self._app.state.daemon is self.state
                     and self.recovery.epoch == self._epoch and self.recovery._publications.epoch == self._epoch
                     and current[0] == original[0] and current[1] == original[1]
                     and current[2] is original[2] and current[3] == original[3]
                     and current[4] is original[4] and current[5] is original[5]
                     and current[6] is original[6] and current[6] is not None
                     and not current[6].is_closed() and current[6].is_running()
                     and current[7] is original[7] and isinstance(original[7], tuple)
                     and len(original[7]) == 5 and original[7][0] is original[2]
                     and original[7][1] == original[1] and original[7][2] == original[3]
                     and not lease.org._startup_read_revoked)
            with self.recovery._lock:
                valid &= not self.recovery._closed and not self.recovery._stop.is_set()
            with self._lock:
                valid &= not self._closed
        except (StartupUnavailable, AttributeError, TypeError) as exc:
            raise StartupReadUnavailable("startup_reader_unavailable") from exc
        if not valid:
            lease.budget.refuse("startup_reader_unavailable")
        lease.budget.check()

    def _validate(self, lease: _Lease) -> None:
        self._validate_references(lease)
        if lease.identity is None or _file_identity(lease.references[3]) != lease.identity:
            lease.budget.refuse("startup_reader_unavailable")
        lease.budget.check()

    async def read(self, org: Any, projection: Callable[[_ThreadReader, _ReadBudget], Any]) -> _ReadResult:
        # Original bearer/OrgDep precede this. One deadline includes reference
        # capture, queue delay, native filesystem/DB/DTO/encoding and publication.
        deadline = time.monotonic() + 2.0
        lease = _Lease(org, self._references(org), _ReadBudget(deadline))
        self.validate_publication(lease)
        if lease.references[6] is not asyncio.get_running_loop():
            raise StartupReadUnavailable("startup_reader_unavailable")
        with self._lock:
            if self._closed or org._startup_read_revoked or len(self._leases) >= 4:
                self._counts["refused"] += 1
                raise StartupReadUnavailable("startup_reader_busy")
            with self.recovery._lock:
                if (self.recovery._stop.is_set() or self.recovery._closed
                        or self.recovery._status == "ready"):
                    raise StartupReadUnavailable("startup_reader_unavailable")
            self._leases.append(lease)
            self._counts["admitted"] += 1
            self._observe("admissions_max", len(self._leases), maximum=True)
            try:
                lease.future = self._executor.submit(self._read, lease, projection)
            except BaseException:
                self._observe("submission_failed")
                self._leases.remove(lease)
                raise
        # Callback owns retirement, outside pool lock (may run synchronously).
        lease.future.add_done_callback(lambda future: self._retire(lease, future))
        try:
            result = await asyncio.shield(asyncio.wrap_future(lease.future))
            await asyncio.shield(asyncio.wrap_future(lease.retired))
            self.validate_publication(lease)
            if not lease.close_proven:
                raise StartupReadUnavailable("startup_reader_close_failed")
            return _ReadResult(result, lease)
        except asyncio.CancelledError:
            self._cancel(lease)
            lease.future.cancel()  # Running native owner retains its slot.
            raise

    def _cancel(self, lease: _Lease) -> None:
        lease.budget.cancelled.set()
        with self._lock:
            if not lease.cancel_recorded:
                self._counts["cancelled"] += 1
                lease.cancel_recorded = True

    def cancel_publication(self, result: _ReadResult) -> None:
        self._cancel(result.lease)

    def validate_publication(self, lease: _Lease) -> None:
        # Filesystem readback stays on the native worker. This final loop-side
        # segment rechecks original references/epoch/stop/deadline without I/O.
        self._validate_references(lease)

    def content_for_publication(self, result: _ReadResult) -> bytes:
        self.validate_publication(result.lease)
        if not result.lease.close_proven or not result.lease.retired.done():
            raise StartupReadUnavailable("startup_reader_close_failed")
        return result.content

    def _read(self, lease: _Lease, projection: Callable[[_ThreadReader, _ReadBudget], Any]) -> bytes:
        conn: sqlite3.Connection | None = None
        result: bytes | None = None
        with self._lock:
            lease.started = True
            lease.started_ns = observation_time_ns()
            self._counts["started"] += 1
        try:
            try:
                self._validate_references(lease)
                lease.identity = lease.references[7][3:]
                self._validate(lease)
                uri = lease.references[3].absolute().as_uri() + "?mode=ro"
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
                # Budget interruption is SQLite's wrapper for the original refusal.
                # Nested cleanup must not replace a genuine earlier decoder/SQL error.
                refusal = (lease.budget.cleanup_refusal if lease.budget.cleanup_primary is exc
                           else lease.budget.refusal)
                lease.primary_error = refusal if isinstance(exc, sqlite3.Error) and refusal is not None else exc
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
                        lease.primary_error.add_note("Startup reader close also failed: " + type(exc).__name__)
            if lease.primary_error is not None:
                if isinstance(lease.primary_error, sqlite3.Error):
                    raise StartupReadUnavailable("startup_reader_unavailable") from lease.primary_error
                raise lease.primary_error
            if not lease.close_proven:
                raise StartupReadUnavailable("startup_reader_close_failed") from lease.close_error
            self._validate(lease)
            assert result is not None
            return result
        finally:
            lease.ended_ns = observation_time_ns()

    def _observe(self, name: str, value: int = 1, *, maximum: bool = False) -> None:
        # Caller owns the original pool mutex; optional telemetry cannot strand
        # the mandatory native close/removal/retirement acknowledgement below.
        try:
            self._counts[name] = max(self._counts[name], value) if maximum else self._counts[name] + value
        except Exception:
            try:
                self._counts["observation_failures"] += 1
            except Exception:
                pass

    def _retire(self, lease: _Lease, future: Future[bytes]) -> None:
        # Observe failures even after the HTTP waiter departed. Only native
        # positive close or successful queued cancellation releases admission.
        error = None if future.cancelled() else future.exception()
        with self._lock:
            if error is not None:
                self._counts["failed"] += 1
            if isinstance(error, StartupReadUnavailable) and error.code == "startup_reader_deadline":
                self._counts["deadline"] += 1
            if lease.budget.refusal is not None and lease.budget.refusal.code == "startup_reader_budget_exhausted":
                self._observe("budget_exhausted")
            for name in ("rows", "raw_bytes", "response_bytes", "opcodes", "attachments", "cleanup_failures"):
                value = getattr(lease.budget, name)
                self._counts[name] += value
                if name != "cleanup_failures":
                    self._observe(name + "_max", value, maximum=True)
            if lease.submitted_ns is not None and lease.started_ns is not None and lease.ended_ns is not None:
                for name, elapsed in (("queue", lease.started_ns - lease.submitted_ns),
                                      ("native", lease.ended_ns - lease.started_ns)):
                    self._observe(name + "_ns_total", elapsed)
                    self._observe(name + "_ns_max", elapsed, maximum=True)
            self._counts["close_failed"] += int(lease.close_error is not None)
            if lease.close_proven or (future.cancelled() and not lease.started):
                self._counts["closed"] += int(lease.close_proven)
                self._observe("retired")
                self._leases.remove(lease)
            # Future.done() precedes callbacks. Acknowledge accounting in the
            # same critical segment as removal, before assert_idle can pass.
            lease.retired.set_result(None)

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
            else:
                org._startup_read_revoked = True
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
            leases = [lease for lease in self._leases if org is None or lease.org is org]
        cancellation: asyncio.CancelledError | None = None
        for lease in leases:
            # Both original native completion AND callback retirement matter.
            for native in (lease.future, lease.retired):
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
                        break  # Actual error observed; close proof still required.
        self.assert_idle(org)
        if org is None:
            self._executor.shutdown(wait=True, cancel_futures=False)
        if cancellation is not None:
            raise cancellation

    def assert_idle(self, org: Any | None = None) -> None:
        with self._lock:
            if any(org is None or lease.org is org for lease in self._leases):
                raise StartupReadUnavailable("startup_reader_close_failed")
