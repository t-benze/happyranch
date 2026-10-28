"""Inert built-in definition registration, owned by normal org recovery only."""
from __future__ import annotations

import json
import logging
import sqlite3
from typing import Any
from dataclasses import dataclass
from datetime import datetime, timezone

from runtime.orchestrator.producer_move import native_writer, owner_for
from runtime.orchestrator.team_move import DeferredTeamMove
from runtime.workflows._coding_registered_release import (
    RegisteredCodingRelease, read_registered_release, registration_rows,
)
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows
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
    pass


class CodingBuiltinStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        # Package I/O happens once, outside every writer/lock. Saved readers
        # never consult this process-local capability observation.
        self._resource = _Resource.capture()

    def available(self, release: RegisteredCodingRelease | None) -> bool:
        return (release is not None and self._resource.definition is not None
                and self._resource.definition == release.template().definition_bytes)

    def _native_suffix(self, conn: sqlite3.Connection, budget: _Budget, states: dict, after: int) -> int:
        """One attempt-local native census, then suffixes on the SAME allowance."""
        from runtime.infrastructure.producer_admission import NativeWriterLifecycle, NativeWriterStore, _unique
        from runtime.workflows._coding_release_history import _copies, _json_work
        budget.step()
        if after == 0 and conn.execute(
                "SELECT 1 FROM audit_log WHERE action='team_move_native_writer' AND id<=0 LIMIT 1").fetchone():
            raise ValueError('coding_builtin_native_history_invalid')
        columns = ('id', 'task_id', 'agent', 'payload')
        for row in _bounded_rows(conn, 'audit_log', columns,
                (('integer',), ('text',), ('text',), ('text',)), budget,
                where="action='team_move_native_writer' AND id>?", args=(after,), value_limit=16384):
            identity, task, agent, payload = row[:4]
            _copies(budget, 4 * len(payload))
            _json_work(payload.encode('utf8'), budget, copies=4, passes=3)
            json.loads(payload, object_pairs_hook=_unique)
            state = NativeWriterLifecycle.model_validate_json(payload, strict=True)
            if (state.org_slug != self.org.slug or task != 'config:team-move-writer:' + state.writer_id
                    or agent != 'native_writer'):
                raise ValueError('coding_builtin_native_history_invalid')
            prior = states.get(state.writer_id)
            if prior is None and state.state != 'active':
                raise ValueError('coding_builtin_native_history_invalid')
            if prior is not None:
                NativeWriterStore.append(prior, state)
            states[state.writer_id] = state
            after = identity
        return after

    def _safety(self, conn: sqlite3.Connection, admission: Any, states: dict, budget: _Budget,
                *, own: str | None = None) -> None:
        if owner_for(self.db) is not admission or admission.move.store.org_slug != self.org.slug:
            raise _Deferred('native_owner_unavailable')
        admission.check_uncommitted(global_write=True)
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
            if conn.execute(f'SELECT 1 FROM {table} WHERE {predicate} LIMIT 1').fetchone():
                raise _Deferred('publication_in_progress')
        row = conn.execute('SELECT state FROM workflow_cutover_state WHERE singleton=1').fetchone()
        if row is None or row['state'] not in ('installed_legacy_only', 'enabled', 'drained'):
            raise _Deferred('cutover_in_progress')

    def ensure_registered(self) -> None:
        """One safe attempt; no retries, repair, authority recovery or takeover."""
        from runtime.infrastructure.workflow_schema import _read_workflow_schema
        from runtime.infrastructure.workflow_coding_schema import _advance_audit_log_watermark
        budget, states, after = _Budget(), {}, 0
        authority = self.org.workflow_authority
        writer = None
        try:
            with authority._publisher_lock, self.db._lock:
                conn = self.db._conn
                if conn is None or conn.in_transaction:
                    raise ValueError('coding_builtin_caller_transaction')
                with self.db.workflow_schema_transaction() as conn:
                    release = read_registered_release(conn, expected_org_slug=self.org.slug, budget=budget)
                    if release is not None:
                        return  # Existing winner: no native reservation or write.
                    if self._resource.definition is None:
                        raise _Deferred(self._resource.error or 'resource_unavailable')
                    admission = owner_for(self.db)
                    if admission is None:
                        raise _Deferred('native_owner_unavailable')
                    after = self._native_suffix(conn, budget, states, after)
                    self._safety(conn, admission, states, budget)
            # Native reservation precedes publisher -> Database lock. The real
            # owner is required; native_writer's ownerless fallback cannot write.
            with native_writer(self.db, publisher='builtin_coding_registration') as writer:
                if writer.writer_id is None:
                    raise _Deferred('native_owner_unavailable')
                advances = []
                with authority._publisher_lock, self.db._lock:
                    with self.db.workflow_schema_transaction() as conn:
                        after = self._native_suffix(conn, budget, states, after)
                        self._safety(conn, admission, states, budget, own=writer.writer_id)
                        _, inventory = _read_workflow_schema(conn, expected_org_slug=self.org.slug,
                                                            budget=budget, advances=advances)
                        if inventory is None:
                            raise ValueError('coding_builtin_storage_required')
                        if inventory.release is None:
                            now = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')
                            from runtime.workflows._coding_release_history import _steps, _copies, _json_work
                            _steps(budget, 4 * 32)
                            _copies(budget, 8192)
                            _json_work(self._resource.definition, budget, copies=4, passes=2)
                            release = registration_rows(self.org.slug, self._resource.definition, now)
                            for table, row in release.rows:
                                conn.execute(f'INSERT INTO {table} ({",".join(row)}) '
                                             f'VALUES ({",".join("?" for _ in row)})', tuple(row.values()))
                            # Discard the source inventory; freshly reconstruct
                            # complete target rows/FKs before this owner's COMMIT.
                            del inventory
                            _, target = _read_workflow_schema(conn, expected_org_slug=self.org.slug,
                                                             budget=budget, advances=advances)
                            if target is None or target.release is None:
                                raise ValueError('coding_builtin_target_invalid')
                        after = self._native_suffix(conn, budget, states, after)
                        self._safety(conn, admission, states, budget, own=writer.writer_id)
                    # Only positively successful COMMIT, under the same DB lock.
                    for file_key, last_id in advances:
                        _advance_audit_log_watermark(conn, file_key, last_id)
        except (_Deferred, DeferredTeamMove) as exc:
            if (self.db._conn is None or self.db._conn.in_transaction
                    or getattr(exc, '__notes__', ())):
                raise  # Cleanup failure must reach the unpublished-load refusal.
            # After a native reservation, a refusal without positive rollback
            # proof remains unknown. Do not relabel or retry that original owner.
            logger.warning('org %r: built-in coding registration deferred (%s)',
                           self.org.slug, str(exc) if isinstance(exc, _Deferred) else 'native_held')
