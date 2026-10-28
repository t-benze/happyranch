"""Inert built-in definition registration, owned by normal org recovery only."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any
from collections.abc import Iterator
from types import TracebackType
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from runtime.orchestrator.producer_move import native_writer, owner_for
from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.workflows._coding_registered_release import (
    RegisteredCodingRelease, read_registered_release, registration_rows,
)
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.release_templates import ReleaseTemplateError, read_coding_release

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Resource:
    definition: bytes | None
    error: str | None

    @classmethod
    def capture(cls) -> _Resource:
        try:
            return cls(read_coding_release(), None)
        except ReleaseTemplateError as exc:
            return cls(None, exc.code)


class _Deferred(ValueError):
    # A refusal raised inside a native cursor is eligible only after that
    # exact invocation has explicitly closed its cursor without secondary error.
    cursor_closed = True


def _require(condition: bool, reason: str = 'native_history_invalid') -> None:
    if not condition:
        raise _Deferred(reason)


@dataclass
class _NativeCensusBudget:
    """Attempt-local allowance; never supplied to complete release readers."""
    consumed: int = 0
    accepted_rows: int = 0
    traversed: int = 0

    def step(self) -> None:
        self.traversed += 1
        _require(self.traversed <= 2097152, 'native_census_visits')

    def candidate(self) -> None:
        self.accepted_rows += 1
        _require(self.accepted_rows <= 8192, 'native_census_rows')

    @property
    def remaining_bytes(self) -> int:
        return 16 * 1048576 - self.consumed


@contextmanager
def _pure_validation(reason: str) -> Iterator[None]:
    """Use only around the named pure parser/accounting/transition operation."""
    try:
        yield
    except _Deferred:
        raise
    except ValueError as primary:
        refusal = _Deferred(reason)
        for note in getattr(primary, '__notes__', ()):
            refusal.add_note(note)
        raise refusal from primary


def _close_cursor(cursor: sqlite3.Cursor, primary: BaseException | None) -> None:
    if isinstance(primary, _Deferred):
        primary.cursor_closed = False
    try:
        cursor.close()
    except BaseException as cleanup:
        if primary is None:
            raise
        primary.add_note('built-in native cursor close failed: ' + type(cleanup).__name__)
    else:
        if isinstance(primary, _Deferred):
            primary.cursor_closed = True


def _probe(conn: sqlite3.Connection, query: str) -> Any:
    cursor, primary = conn.cursor(), None
    try:
        return cursor.execute(query).fetchone()
    except BaseException as exc:
        primary = exc
        raise
    finally:
        _close_cursor(cursor, primary)


class _ExitProof:
    """Observe the actual context exit returning, including on the same error.

    Merely seeing the original exception outside a context cannot prove that
    its exit returned rather than raising that same exception during cleanup.
    """
    def __init__(self, context: Any) -> None:
        self.context = context
        self.entered = self.exited = self.suppressed = False

    def __enter__(self) -> Any:
        value = self.context.__enter__()
        self.entered = True
        return value

    def __exit__(self, kind: type[BaseException] | None, error: BaseException | None,
                 traceback: TracebackType | None) -> Any:
        result = self.context.__exit__(kind, error, traceback)
        self.suppressed = bool(result) and error is not None
        self.exited = True
        return result


class _RegistrationPhase:
    def __init__(self, authority: Any, db: Any) -> None:
        self.publisher = _ExitProof(authority._publisher_lock)
        self.database = _ExitProof(db._lock)
        self.transaction = _ExitProof(db.workflow_schema_transaction())
        self.conn = None
        self.refusal = None
        self.committed = False

    def capture(self, db: Any) -> None:
        self.conn = db._conn
        if type(self.conn) is not sqlite3.Connection or self.conn.in_transaction:
            raise ValueError('coding_builtin_caller_transaction')

    def rolled_back(self, db: Any, error: _Deferred) -> bool:
        return (self.refusal is error and not self.committed
                and error.cursor_closed and not getattr(error, '__notes__', ())
                and all(p.entered and p.exited and not p.suppressed
                        for p in (self.transaction, self.database, self.publisher))
                and self.conn is db._conn and type(self.conn) is sqlite3.Connection
                and not self.conn.in_transaction)


class CodingBuiltinStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        # Package I/O happens once, outside every writer/lock. Saved readers
        # never consult this process-local capability observation.
        self._resource = _Resource.capture()

    def available(self, release: RegisteredCodingRelease | None) -> bool:
        return (release is not None and self._resource.definition is not None
                and self._resource.definition == release.template().definition_bytes)

    def _native_suffix(self, conn: sqlite3.Connection, budget: _NativeCensusBudget,
                       states: dict, after: int) -> int:
        """Own acquisition AND consumption lexically, including explicit close.

        This is the four-column _bounded_rows projection: singleton rowid
        keysets, whole-row gates before copying, original storage classes and
        per-cell caps, rejected/terminal page charges and cumulative accounting.
        SQL/cursor errors are deliberately outside pure-validation adapters.
        """
        from runtime.infrastructure.producer_admission import NativeWriterLifecycle, NativeWriterStore, _unique
        from runtime.workflows._coding_release_history import _copies, _json_work
        names = ('"id"', '"task_id"', '"agent"', '"payload"')
        allowed = ('integer', 'text', 'text', 'text')
        size = '+'.join(f"CASE WHEN typeof({name}) IN ('text','blob') "
                        f"THEN length(CAST({name} AS BLOB)) ELSE 0 END" for name in names)
        gates = [f"(typeof({name})='{kind}' AND (typeof({name}) NOT IN ('text','blob') "
                 f"OR length(CAST({name} AS BLOB))<=16384))"
                 for name, kind in zip(names, allowed, strict=True)]
        valid = ' AND '.join(gates) + ' AND ? > 0 AND (' + size + ')<=?'
        query = ('WITH candidate AS (SELECT rowid AS _scan_rowid,' + ','.join(names)
                 + " FROM audit_log WHERE action='team_move_native_writer' AND id>?"
                 + ' AND rowid>=? ORDER BY rowid LIMIT 1), '
                 + 'gated AS (SELECT *,(' + valid + ') AS _scan_valid FROM candidate) '
                 + 'SELECT _scan_rowid,_scan_valid,'
                 + ','.join(f'CASE WHEN _scan_valid THEN {name} ELSE NULL END' for name in names)
                 + ',' + ','.join(f'typeof({name})' for name in names) + ' FROM gated')
        cursor, primary = conn.cursor(), None
        try:
            cursor.row_factory = None
            budget.step()
            if after == 0:
                cursor.execute("SELECT 1 FROM audit_log WHERE action='team_move_native_writer' AND id<=0 LIMIT 1")
                _require(cursor.fetchone() is None)
            minimum, count, suffix = -9223372036854775808, 0, after
            while True:
                budget.step()
                cursor.execute(query, (suffix, minimum, min(8192 - count,
                    8192 - budget.accepted_rows), budget.remaining_bytes))
                row = cursor.fetchone()
                if row is None:
                    return after
                budget.candidate()
                count += 1
                _require(count <= 8192 and row[1] == 1)
                identity, values, types = row[0], row[2:6], row[6:]
                _require(type(identity) is int and identity >= minimum)
                for value, storage, kind in zip(values, types, allowed, strict=True):
                    budget.step()
                    _require(storage == kind and type(value) is (int if kind == 'integer' else str))
                    if storage == 'text':
                        with _pure_validation('native_history_utf8'):
                            raw = value.encode('utf8')
                        budget.consumed += len(raw)
                        _require(len(raw) <= 16384 and budget.remaining_bytes >= 0, 'native_census_bytes')
                native_id, task, agent, payload = values
                _require(type(native_id) is int and native_id == identity and native_id > after)
                with _pure_validation('native_census_bytes'):
                    _copies(budget, 4 * len(payload))
                with _pure_validation('native_history_json_work'):
                    _json_work(payload.encode('utf8'), budget, copies=4, passes=3)
                with _pure_validation('native_history_duplicate_json'):
                    json.loads(payload, object_pairs_hook=_unique)
                with _pure_validation('native_history_lifecycle'):
                    state = NativeWriterLifecycle.model_validate_json(payload, strict=True)
                _require(state.org_slug == self.org.slug
                         and task == 'config:team-move-writer:' + state.writer_id
                         and agent == 'native_writer')
                prior = states.get(state.writer_id)
                _require(prior is not None or state.state == 'active')
                if prior is not None:
                    with _pure_validation('native_history_transition'):
                        NativeWriterStore.append(prior, state)
                states[state.writer_id] = state
                after = native_id
                if identity == 9223372036854775807:
                    return after
                minimum = identity + 1
        except BaseException as exc:
            primary = exc
            raise
        finally:
            _close_cursor(cursor, primary)

    def _safety(self, conn: sqlite3.Connection, admission: Any, states: dict, budget: _NativeCensusBudget,
                *, own: str | None = None) -> None:
        if owner_for(self.db) is not admission or admission.move.store.org_slug != self.org.slug:
            raise _Deferred('native_owner_unavailable')
        try:
            admission.check_uncommitted(global_write=True)
        except DeferredTeamMove as primary:
            # Only this actual admission refusal is optional, never owner_for,
            # native entry, SQL/decode or complete-reader errors.
            refusal = _Deferred('native_held')
            for note in getattr(primary, '__notes__', ()):
                refusal.add_note(note)
            raise refusal from primary
        def competing_builtin(key: str) -> bool:
            # A second actual live reservation under THIS mounted native owner
            # can serialize on the same publisher/SQL lock and retain its winner.
            # A stale durable active/unknown record alone supplies no live owner.
            state, live = states.get(key), admission.writers.get(key)
            return (state is not None and state.state == 'active'
                    and state.publisher == 'builtin_coding_registration'
                    and live is not None and live['publisher'] == state.publisher
                    and not state.agents and not state.teams
                    and not live['agents'] and not live['teams'])

        for key, state in states.items():
            budget.step()
            if state.state != 'settled' and key != own and not competing_builtin(key):
                raise _Deferred('native_writer_unsettled')
        for key in admission.writers:
            budget.step()
            if key != own and not competing_builtin(key):
                raise _Deferred('native_writer_active')
        for table, predicate in (
            ('workflow_publication_leases', '1'), ('workflow_profile_leases', '1'),
            ('workflow_publication_journals', "state NOT IN ('cache_installed','aborted')"),
            ('workflow_profile_operations', "state NOT IN ('published','aborted')"),
        ):
            budget.step()
            if _probe(conn, f'SELECT 1 FROM {table} WHERE {predicate} LIMIT 1'):
                raise _Deferred('publication_in_progress')
        budget.step()
        row = _probe(conn, 'SELECT state FROM workflow_cutover_state WHERE singleton=1')
        if row is None or row['state'] not in ('installed_legacy_only', 'enabled', 'drained'):
            raise _Deferred('cutover_in_progress')

    def _warn_deferred(self, error: _Deferred) -> None:
        logger.warning('org %r: built-in coding registration deferred (%s); '
                       'restore the packaged resource or resolve the original native/recovery safety owner',
                       self.org.slug, str(error)[:128])

    def _writer_owned(self, admission: Any, writer: Any, reservation: dict | None) -> bool:
        with self.db._lock:
            return (owner_for(self.db) is admission and reservation is not None
                    and admission.move.store.org_slug == self.org.slug
                    and admission.writers.get(writer.writer_id) is reservation
                    and reservation == dict(owner_id=writer.writer_id,
                        publisher='builtin_coding_registration', agents=(), teams=()))

    def ensure_registered(self) -> None:
        """One safe attempt; no retries, repair, authority recovery or takeover."""
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        from runtime.infrastructure.workflow_coding_schema import _advance_audit_log_watermark
        budget, census, states, after = _Budget(), _NativeCensusBudget(), {}, 0
        authority = self.org.workflow_authority
        phase = _RegistrationPhase(authority, self.db)
        try:
            with phase.publisher, phase.database:
                phase.capture(self.db)
                with phase.transaction as conn:
                    if conn is not phase.conn:
                        raise RuntimeError('coding_builtin_connection_changed')
                    try:
                        release = read_registered_release(conn, expected_org_slug=self.org.slug, budget=budget)
                        if release is not None:
                            return  # Existing winner: no native reservation or write.
                        if self._resource.definition is None:
                            raise _Deferred(self._resource.error or 'resource_unavailable')
                        admission = owner_for(self.db)
                        if admission is None:
                            raise _Deferred('native_owner_unavailable')
                        after = self._native_suffix(conn, census, states, after)
                        self._safety(conn, admission, states, census)
                    except _Deferred as exc:
                        phase.refusal = exc
                        raise
                phase.committed = True
        except _Deferred as exc:
            if not phase.rolled_back(self.db, exc):
                raise
            self._warn_deferred(exc)
            return

        # Entry is outside preflight's safe interval: failure before yielding
        # an actual durable writer is never proof that no reservation happened.
        writer, reservation = None, None
        native = _ExitProof(native_writer(self.db, publisher='builtin_coding_registration'))
        try:
            with native as writer:
                if writer.writer_id is None:
                    raise RuntimeError('coding_builtin_native_owner_unavailable')
                with self.db._lock:
                    reservation = admission.writers.get(writer.writer_id)
                    if not self._writer_owned(admission, writer, reservation):
                        raise RuntimeError('coding_builtin_native_owner_changed')
                phase = _RegistrationPhase(authority, self.db)
                advances = []
                try:
                    with phase.publisher, phase.database:
                        phase.capture(self.db)
                        with phase.transaction as conn:
                            if conn is not phase.conn:
                                raise RuntimeError('coding_builtin_connection_changed')
                            try:
                                after = self._native_suffix(conn, census, states, after)
                                self._safety(conn, admission, states, census, own=writer.writer_id)
                                _, inventory = _read_workflow_schema(conn, expected_org_slug=self.org.slug,
                                                                    budget=budget, advances=advances)
                                if inventory is None:
                                    raise ValueError('coding_builtin_storage_required')
                                if inventory.release is None:
                                    now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                                    from runtime.workflows._coding_release_history import _steps, _copies, _json_work
                                    with _pure_validation('registration_expansion_visits'):
                                        _steps(budget, 4 * 32)
                                    with _pure_validation('registration_expansion_bytes'):
                                        _copies(budget, 8192)
                                    with _pure_validation('registration_expansion_json_work'):
                                        _json_work(self._resource.definition, budget, copies=4, passes=2)
                                    release = registration_rows(self.org.slug, self._resource.definition, now)
                                    for table, row in release.rows:
                                        conn.execute(f'INSERT INTO {table} ({",".join(row)}) '
                                                     f'VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
                                    # Reconstruct complete target rows/FKs before this owner's COMMIT.
                                    del inventory
                                    _, target = _read_workflow_schema(conn, expected_org_slug=self.org.slug,
                                                                     budget=budget, advances=advances)
                                    if target is None or target.release is None:
                                        raise ValueError('coding_builtin_target_invalid')
                                after = self._native_suffix(conn, census, states, after)
                                self._safety(conn, admission, states, census, own=writer.writer_id)
                            except _Deferred as exc:
                                phase.refusal = exc
                                raise
                        phase.committed = True
                        # Positive original COMMIT only, under the SAME DB lock.
                        for file_key, last_id in advances:
                            _advance_audit_log_watermark(conn, file_key, last_id)
                except _Deferred as exc:
                    if (not phase.rolled_back(self.db, exc)
                            or not self._writer_owned(admission, writer, reservation)):
                        raise
                    # Actual cursor close, original rollback and BOTH outer
                    # exits have returned. Mark only this exact body refusal,
                    # while still inside the original native_writer interval.
                    with writer.validation():
                        raise
        except _Deferred as exc:
            if (not native.entered or not native.exited or native.suppressed
                    or writer is None or writer.writer_id is None
                    or writer.no_effect_error is not exc or writer.uncertain
                    or not phase.rolled_back(self.db, exc)):
                raise
            with self.db._lock:
                if (owner_for(self.db) is not admission
                        or admission.move.store.org_slug != self.org.slug
                        or writer.writer_id in admission.writers):
                    raise
            # The genuine native exit settled before removing its registry
            # entry; a failed settlement adds notes or raises and cannot warn.
            self._warn_deferred(exc)
