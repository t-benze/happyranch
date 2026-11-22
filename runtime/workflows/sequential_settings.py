"""Repo-free Founder settings CAS; immutable original receipts, never work."""
from __future__ import annotations

import hashlib
import sqlite3
import time
from contextlib import ExitStack
from typing import Annotated, Any, Callable, Literal

from pydantic import BeforeValidator, Field, StrictInt, WithJsonSchema, field_serializer

from runtime.orchestrator.producer_move import native_writer
from runtime.workflows.sequential_contracts import (
    BindingClaim, Closed, DefinitionPin, Digest, Limits, Positive, Token, _array,
    GenericDefinitionPin, GenericLimits,
)
from runtime.workflows.sequential_definition import _json, compile_definition, compile_generic_definition, generic_limits
from runtime.workflows.sequential_engine import _bound_agents, _engine_rows, _identity
from runtime.workflows.sequential_history import (
    StorageSettings, GenericStorageSettings, _storage_settings, _generic_limits_value, _GENERIC_LIMITS_SCHEMA,
    _ReadBudget, _bytes, _decode, _parse, _sql_budget, read_sequential_history,
)
from runtime.workflows.sequential_coding_evidence import _current_authority
from runtime.workflows.sequential_views import ProjectionRefusal, SettingsView, GenericSettingsView

COMMAND_LIMIT = 16384
PRINCIPAL = 'founder'


class SettingsCommand(Closed):
    format: Literal['workflow-sequential-settings-put@1']
    operation_key: Token
    expected_generation: Annotated[StrictInt, Field(ge=0, le=9223372036854775806)]
    definition: DefinitionPin
    bindings: Annotated[tuple[BindingClaim, ...], BeforeValidator(_array), Field(min_length=1, max_length=16)]
    limits: Limits


class GenericSettingsCommand(SettingsCommand):
    format: Literal['workflow-sequential-settings-put@2']
    definition: GenericDefinitionPin
    limits: Annotated[GenericLimits, WithJsonSchema(_GENERIC_LIMITS_SCHEMA)]

    @field_serializer('limits')
    def limits_value(self, value: GenericLimits) -> dict:
        return _generic_limits_value(value)


class AdmissionCommand(Closed):
    format: Literal['workflow-sequential-admission@1']
    operation_key: Token
    expected_generation: Annotated[StrictInt, Field(ge=0, le=9223372036854775806)]
    definition: DefinitionPin | GenericDefinitionPin
    admission: Literal['Allowed', 'Disabled']


SetupCommand = SettingsCommand | GenericSettingsCommand | AdmissionCommand
Snapshot = StorageSettings | GenericStorageSettings


class StorageReceipt(Closed):
    format: Literal['workflow-sequential-settings-receipt-storage@1']
    operation_id: str
    settings_id: str
    generation: Positive
    snapshot_digest: Digest


class SettingsReceipt(Closed):
    format: Literal['workflow-sequential-settings-receipt@1'] = 'workflow-sequential-settings-receipt@1'
    org_slug: str
    operation_key: Token
    command: SettingsCommand
    receipt: StorageReceipt
    original: SettingsView


class GenericSettingsReceipt(SettingsReceipt):
    format: Literal['workflow-sequential-settings-receipt@2'] = 'workflow-sequential-settings-receipt@2'
    command: GenericSettingsCommand
    original: GenericSettingsView


class AdmissionReceipt(Closed):
    format: Literal['workflow-sequential-admission-receipt@1'] = 'workflow-sequential-admission-receipt@1'
    org_slug: str
    operation_key: Token
    command: AdmissionCommand
    receipt: StorageReceipt
    original: SettingsView | GenericSettingsView


SetupReceipt = SettingsReceipt | GenericSettingsReceipt | AdmissionReceipt


def parse_settings_command(raw: bytes) -> SettingsCommand:
    return SettingsCommand.model_validate(_json(raw, COMMAND_LIMIT))


def _original(conn: sqlite3.Connection, org: Any, key: str, budget: _ReadBudget) -> SetupReceipt | None:
    operations = _engine_rows(conn, 'workflow_seq_settings_operations', budget,
        'org_slug=? AND principal=? AND operation_key=?', (org.slug, PRINCIPAL, key))
    if not operations:
        return None
    operation = operations[0]
    row = _engine_rows(conn, 'workflow_seq_settings', budget, 'id=?', (operation['settings_id'],))[0]
    snapshot = _storage_settings(row['snapshot_bytes'], budget)
    saved = _decode(operation['request_bytes'], budget)
    generic = isinstance(snapshot, GenericStorageSettings)
    view = GenericSettingsView if generic else SettingsView
    if saved['format'] == 'workflow-sequential-admission-operation-storage@1':
        command = AdmissionCommand(format='workflow-sequential-admission@1', operation_key=key,
            expected_generation=saved['expected_generation'], definition=snapshot.definition,
            admission=snapshot.admission)
        owner = AdmissionReceipt
    else:
        model = GenericSettingsCommand if generic else SettingsCommand
        command = model(format='workflow-sequential-settings-put@2' if generic else 'workflow-sequential-settings-put@1',
            operation_key=key, expected_generation=saved['expected_generation'], definition=snapshot.definition,
            bindings=snapshot.bindings, limits=snapshot.limits)
        owner = GenericSettingsReceipt if generic else SettingsReceipt
    receipt = StorageReceipt.model_validate(_decode(operation['receipt_bytes'], budget))
    return owner(org_slug=org.slug, operation_key=key, command=command, receipt=receipt,
        original=view(id=row['id'], generation=row['generation'],
            snapshot_digest=row['snapshot_digest'], snapshot=snapshot))


def _selection(conn: sqlite3.Connection, org: Any, command: SetupCommand, capture: Any,
               budget: _ReadBudget) -> tuple[dict, Snapshot, int]:
    definitions = _engine_rows(conn, 'workflow_seq_definitions', budget,
        'identity=? AND version=?', (command.definition.identity, command.definition.version))
    if not definitions:
        raise ProjectionRefusal('sequential_definition_not_found', 404)
    definition = definitions[0]
    compiler = (compile_generic_definition if type(command) is not SettingsCommand
                and definition['compiler_pin'] == 'workflow-sequential-compiler@2' else compile_definition)
    compiled = _parse(definition['definition_bytes'], compiler, budget,
                      source_digest=definition['source_digest'])
    if command.definition != compiled.pin:
        raise ProjectionRefusal('sequential_definition_conflict')
    rows = _engine_rows(conn, 'workflow_seq_settings', budget,
        'org_slug=? AND definition_identity=?', (org.slug, command.definition.identity))
    current = max(rows, key=lambda row: row['generation']) if rows else None
    generation = 0 if current is None else current['generation']
    if command.expected_generation != generation:
        raise ProjectionRefusal('sequential_settings_generation_stale')
    if isinstance(command, AdmissionCommand):
        if current is None:
            raise ProjectionRefusal('sequential_settings_defaults_required')
        saved = _storage_settings(current['snapshot_bytes'], budget)
        if saved.definition != command.definition:
            raise ProjectionRefusal('sequential_settings_defaults_required')
        # Admission never resolves, fills or normalizes the saved defaults/limits.
        return definition, saved.model_copy(update={'admission': command.admission}), generation
    bindings = {item.role: item.principal for item in command.bindings}
    if (len(bindings) != len(command.bindings) or bindings.keys() != dict(compiled.definition.roles).keys()
            or any(bindings[role] == bindings[other] for role, requirement in compiled.definition.roles
                   for other in requirement.independent_of)
            or any(getattr(command.limits, name) > getattr(compiled.definition.limits, name)
                   for name in (('max_visits', 'max_events', 'max_recoveries')
                                if isinstance(command, GenericSettingsCommand) else Limits.model_fields))):
        raise ProjectionRefusal('sequential_settings_defaults_invalid', 422)
    if isinstance(command, GenericSettingsCommand):
        try:
            effective = generic_limits(compiled)
            revisions = dict(effective.node_revisions)
            if any(node not in revisions or value > revisions[node] for node, value in command.limits.node_revisions):
                raise ValueError('invalid_revision_bound')
        except ValueError:
            raise ProjectionRefusal('sequential_settings_defaults_invalid', 422) from None
    budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
    try:
        # This existing helper consumes bindings only; it grants no access.
        agents = _bound_agents(compiled, command, capture)
    except ValueError as exc:
        if str(exc) in ('workflow_seq_role_unavailable', 'workflow_seq_role_ineligible'):
            raise ProjectionRefusal('sequential_authority_denied', 403) from None
        raise
    # All selected roles belong to the registered manager's team. No repository
    # or effective-access observation belongs in setup.
    managers = [agents[role] for role, requirement in compiled.definition.roles
                if requirement.eligibility == 'registered-team-manager']
    if managers and any(agent['team'] != managers[0]['team'] for agent in agents.values()):
        raise ProjectionRefusal('sequential_authority_denied', 403)
    admission = 'Disabled' if current is None else _decode(current['snapshot_bytes'], budget)['admission']
    owner = GenericStorageSettings if isinstance(command, GenericSettingsCommand) else StorageSettings
    snapshot = owner(format='workflow-sequential-settings-storage@2' if owner is GenericStorageSettings
                     else 'workflow-sequential-settings-storage@1', definition=compiled.pin,
        bindings=command.bindings, limits=command.limits, admission=admission)
    return definition, snapshot, generation


def _inspect(conn: sqlite3.Connection, org: Any, capture: Any, budget: _ReadBudget,
             deadline: float, key: str, command: SetupCommand | None, kind: str) -> SetupReceipt | None:
    with _sql_budget(conn, budget):
        _current_authority(conn, org.workflow_authority, capture, budget)
    history = read_sequential_history(conn, expected_org_slug=org.slug, deadline=deadline)
    if not history.layout.sequential:
        raise ProjectionRefusal('sequential_storage_unavailable')
    # Complete reader clears its own SQL hook; caller restores its original hook.
    with _sql_budget(conn, budget):
        receipt = _original(conn, org, key, budget)
        if receipt is not None:
            if receipt.command.format != kind:
                raise ProjectionRefusal('sequential_settings_operation_conflict')
            if command is not None and command != receipt.command:
                raise ProjectionRefusal('sequential_settings_operation_conflict')
        elif command is None:
            raise ProjectionRefusal('sequential_settings_operation_not_found', 404)
        else:
            _selection(conn, org, command, capture, budget)
        _current_authority(conn, org.workflow_authority, capture, budget)
        return receipt


async def settings_operation(org: Any, *, key: str, command: SetupCommand | None,
                             revalidate: Callable[[], None], kind: str = 'workflow-sequential-settings-put@1') -> SetupReceipt:
    """SELECT-only lookup/replay; fresh writes recheck under the original writer.

    One deadline; each complete oracle retains its OWN D2 allowance. Projection,
    command and write work share one finite allowance. No retries or work IDs.
    """
    if command is not None and (command.operation_key != key or command.format != kind):
        raise ProjectionRefusal('sequential_settings_request_invalid', 422)
    deadline = time.monotonic() + 5.0
    budget = _ReadBudget(deadline=deadline)
    authority = org.workflow_authority
    capture = authority.capture_admission()
    async with authority._async_writer_lock, org.teams_lock, org.db_lock:
        # Exact replay and read-only recovery never acquire a durable write lease.
        with ExitStack() as fences:
            for name in capture.profile_names:
                fences.enter_context(authority._profile_coordinator.profile_read(name))
            fences.enter_context(authority._publisher_lock)
            fences.enter_context(org.db._lock)
            conn = org.db._conn
            if conn.in_transaction:
                raise ProjectionRefusal('sequential_reader_transaction')
            conn.execute('BEGIN')
            try:
                revalidate()
                if command is None and conn.execute(
                        'SELECT 1 FROM workflow_seq_settings_operations WHERE org_slug=? AND principal=? AND operation_key=?',
                        (org.slug, PRINCIPAL, key)).fetchone() is None:
                    raise ProjectionRefusal('sequential_settings_operation_not_found', 404)
                with _sql_budget(conn, budget):
                    receipt = _inspect(conn, org, capture, budget, deadline, key, command, kind)
                    if receipt is not None:
                        budget.reserve(96 * len(_bytes(receipt.model_dump(mode='json'))) + 4096)
                    revalidate()
            finally:
                conn.rollback()
        if receipt is not None:
            return receipt
        assert command is not None
        # Recheck all saved ownership, original key/body and current CAS after
        # entering the supported writer; a concurrent publication cannot slip in.
        try:
            with native_writer(org.db, publisher='org_settings'):
                with authority.admission_writer(capture) as conn:
                    revalidate()
                    with _sql_budget(conn, budget):
                        receipt = _inspect(conn, org, capture, budget, deadline, key, command, kind)
                    with _sql_budget(conn, budget):
                        if receipt is None:
                            definition, snapshot, generation = _selection(conn, org, command, capture, budget)
                            raw = _bytes(snapshot.model_dump(mode='json'))
                            digest = hashlib.sha256(raw).hexdigest()
                            setting_id = _identity('settings', org.slug, PRINCIPAL, key)
                            operation_id = _identity('settings-operation', org.slug, PRINCIPAL, key)
                            request = _bytes(dict(format=('workflow-sequential-admission-operation-storage@1'
                                if isinstance(command, AdmissionCommand) else 'workflow-sequential-settings-operation-storage@1'),
                                operation_key=key, principal=PRINCIPAL, settings_id=setting_id,
                                expected_generation=generation, snapshot_digest=digest))
                            stored_receipt = _bytes(dict(format='workflow-sequential-settings-receipt-storage@1',
                                operation_id=operation_id, settings_id=setting_id,
                                generation=generation + 1, snapshot_digest=digest))
                            conn.execute('INSERT INTO workflow_seq_settings '
                                '(id,org_slug,definition_id,definition_identity,generation,snapshot_bytes,snapshot_digest) '
                                'VALUES (?,?,?,?,?,?,?)', (setting_id, org.slug, definition['id'],
                                    command.definition.identity, generation + 1, raw, digest))
                            conn.execute('INSERT INTO workflow_seq_settings_operations '
                                '(id,org_slug,principal,operation_key,request_bytes,request_digest,settings_id,receipt_bytes,receipt_digest) '
                                'VALUES (?,?,?,?,?,?,?,?,?)', (operation_id, org.slug, PRINCIPAL, key, request,
                                    hashlib.sha256(request).hexdigest(), setting_id, stored_receipt,
                                    hashlib.sha256(stored_receipt).hexdigest()))
                            read_sequential_history(conn, expected_org_slug=org.slug, deadline=deadline)
                            with _sql_budget(conn, budget):
                                receipt = _original(conn, org, key, budget)
                    with _sql_budget(conn, budget):
                        if receipt is None:
                            raise ProjectionRefusal('sequential_settings_receipt_unverified')
                        budget.reserve(96 * len(_bytes(receipt.model_dump(mode='json'))) + 4096)
                        _current_authority(conn, authority, capture, budget)
                        revalidate()
        except ProjectionRefusal as exc:
            raise ProjectionRefusal('sequential_settings_publication_uncertain') from exc
        # COMMIT/lease-release failures propagate: never report an invented receipt.
        budget.check()
        return receipt
