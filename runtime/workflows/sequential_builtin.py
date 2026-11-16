"""Qualified, definition-only registration of the literal reviewed catalog.

Missing defaults mean Disabled and unset roles. This owner never allocates
settings, admission, runs or tasks, and never repairs a conflicting definition.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from runtime.orchestrator.producer_move import native_writer, owner_for
from runtime.workflows.coding_builtin import _Deferred, _NativeCensusBudget, _RegistrationPhase
from runtime.workflows.coding_authority_history import _Budget
from runtime.workflows.sequential_definition import CompiledDefinition
from runtime.workflows.sequential_engine import _engine_rows, _identity
from runtime.workflows.sequential_history import _ReadBudget, _sql_budget, read_sequential_history
from runtime.workflows.sequential_release import read_coding_release

# Deliberately literal: package discovery cannot silently enlarge this catalog.
PRESET_CATALOG = ((
    'release/happyranch/coding', 2,
    '5039e9eb60a03f8de110b40123a123d25962fbff48c39af5271a0666d679d065',
    'a3b012d117c5905a1bac3c67a3158a8b1afa5a571acdba73c876ac7de24f790c',
),)


def preset_catalog() -> tuple[CompiledDefinition, ...]:
    """Read the one fixed reviewed resource outside database scopes."""
    compiled = read_coding_release()
    pin = compiled.pin
    if ((pin.identity, pin.version, pin.source_digest, pin.definition_digest),) != PRESET_CATALOG:
        raise ValueError('sequential_builtin_catalog_mismatch')
    return (compiled,)


def inspect_definition(conn: sqlite3.Connection, compiled: CompiledDefinition,
                       budget: _ReadBudget) -> tuple[dict, bool]:
    """Shared live/offline exact immutable-row selection; no writes."""
    pin = compiled.pin
    if (pin.identity, pin.version, pin.source_digest, pin.definition_digest) not in PRESET_CATALOG:
        raise ValueError('sequential_builtin_catalog_mismatch')
    definitions = _engine_rows(conn, 'workflow_seq_definitions', budget,
        'definition_digest=? OR (identity=? AND version=?)',
        (pin.definition_digest, pin.identity, pin.version))
    definition = dict(id=_identity('definition', pin.definition_digest), identity=pin.identity,
        version=pin.version, definition_bytes=compiled.raw, definition_digest=pin.definition_digest,
        source_digest=pin.source_digest, compiler_pin=pin.compiler, validator_pin=pin.validator,
        interpreter_pin=pin.interpreter, capability_pin=pin.capabilities)
    if definitions:
        # Compatible existing publishers may use another immutable row ID.
        definition['id'] = definitions[0]['id']
        if len(definitions) != 1 or definitions[0] != definition:
            raise ValueError('sequential_coding_definition_conflict')
    return definition, bool(definitions)


class SequentialBuiltinStore:
    def __init__(self, org: Any) -> None:
        self.org, self.db = org, org.db
        self.catalog = preset_catalog()

    def ensure_registered(self) -> None:
        """One attempt after real load/recovery; exact existing rows write zero.

        Definition registration uses the existing bounded native census/safety
        owner without moving its implementation or changing its module globals.
        Its live reservation is nondurable: definition bytes are the only DML.
        Publisher and database locks serialize the original readiness capture,
        reservation and owning transaction; no authority is published here.
        """
        db, authority = self.db, self.org.workflow_authority
        budget = _ReadBudget()
        retained = _Budget()
        phase = _RegistrationPhase(authority, db)
        try:
            with phase.publisher, phase.database:
                if db._conn.in_transaction:
                    raise ValueError('sequential_builtin_caller_transaction')
                phase.capture(db)
                # Legacy orgs stay legacy; an absent DB/table never proves freshness.
                if db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='workflow_seq_schema_versions'").fetchone() is None:
                    return
                with db.workflow_schema_transaction() as conn, _sql_budget(conn, budget):
                    pending = [compiled for compiled in self.catalog
                               if not inspect_definition(conn, compiled, budget)[1]]
                if not pending:
                    return
                if self.org._startup_publication_held or self.org._startup_authority_error is not None:
                    return
                ready = authority.verify_admission_ready()
                pointer = authority._pointer(db._conn, authority.namespace)
                admission = owner_for(db)
                if admission is None:
                    raise ValueError('sequential_builtin_native_owner_unavailable')
                census, states = _NativeCensusBudget(), {}
                native = self.org.workflow_coding_builtin
                # No transaction/lease is open while the actual file/cache readiness
                # is checked above. Subsequent qualification is SQL-only.
                with phase.transaction as conn, _sql_budget(conn, budget):
                    if conn is not phase.conn:
                        raise RuntimeError('sequential_builtin_connection_changed')
                    try:
                        after = native._native_suffix(conn, census, states, 0)
                        native._safety(conn, admission, states, census)
                    except _Deferred as exc:
                        phase.refusal = exc
                        raise
                phase.committed = True
                before = set(admission.writers)
                with native_writer(db, publisher='builtin_sequential_registration', durable=False):
                    added = set(admission.writers) - before
                    if owner_for(db) is not admission or len(added) != 1:
                        raise ValueError('sequential_builtin_native_owner_changed')
                    own = added.pop()
                    reservation = admission.writers[own]
                    if reservation != dict(owner_id=own, publisher='builtin_sequential_registration', agents=(), teams=()):
                        raise ValueError('sequential_builtin_native_owner_changed')
                    with db.workflow_schema_transaction() as conn:
                        history = read_sequential_history(conn, expected_org_slug=self.org.slug,
                            retained_budget=retained, deadline=budget.deadline)
                        if not history.layout.sequential or history.layout.ownership != 2:
                            raise ValueError('sequential_builtin_storage_required')
                        with _sql_budget(conn, budget):
                            after = native._native_suffix(conn, census, states, after)
                            native._safety(conn, admission, states, census, own=own)
                            if (authority._pointer(conn, authority.namespace) != pointer
                                    or authority._cache.get(ready.namespace) != (ready.generation, ready.snapshot_digest)):
                                raise ValueError('sequential_builtin_authority_changed')
                            changes, inserted = conn.total_changes, 0
                            for compiled in pending:
                                definition, exists = inspect_definition(conn, compiled, budget)
                                if not exists:
                                    columns = ','.join(definition)
                                    conn.execute(f'INSERT INTO workflow_seq_definitions ({columns}) '
                                        f'VALUES ({",".join("?" for _ in definition)})', tuple(definition.values()))
                                    inserted += 1
                            # Complete source qualification authenticated every
                            # existing row and the literal INSERT trigger/schema.
                            # Only compiled catalog bytes were appended. Verify that
                            # exact delta, not another full retained-history scan on
                            # the SAME bounded ledger (or a replenished allowance).
                            for compiled in pending:
                                if not inspect_definition(conn, compiled, budget)[1]:
                                    raise ValueError('sequential_builtin_target_missing')
                            if (conn.total_changes - changes != inserted
                                    or conn.execute('PRAGMA foreign_key_check(workflow_seq_definitions)').fetchone() is not None):
                                raise ValueError('sequential_builtin_target_mismatch')
                            native._native_suffix(conn, census, states, after)
                            native._safety(conn, admission, states, census, own=own)
                            if (owner_for(db) is not admission or admission.writers.get(own) is not reservation
                                    or authority._pointer(conn, authority.namespace) != pointer):
                                raise ValueError('sequential_builtin_qualification_changed')
                            budget.check()
        except _Deferred as exc:
            # Only the original preflight cutover refusal may leave ordinary
            # loading available. Reuse CodingBuiltinStore's positive proof of
            # cursor closure, rollback, connection identity and BOTH lock exits.
            # Native uncertainty and every later writer-phase refusal propagate.
            if str(exc) != 'cutover_in_progress' or not phase.rolled_back(db, exc):
                raise
            native._warn_deferred(exc)
