"""Additive Start@2 advisory discovery; native callback admission is authoritative."""
from __future__ import annotations

import asyncio
import sqlite3
import time
from contextlib import ExitStack
from typing import Annotated, Any, Callable, Literal

from pydantic import WithJsonSchema, field_serializer

from runtime.infrastructure.workflow_task_ownership import selected_owner
from runtime.infrastructure.task_pause_controls import MAX_GENERATION
from runtime.workflows.coding_authority_history import _bounded_rows
from runtime.workflows.sequential_assignments import AssignmentRefusal, resolve_assignments
from runtime.workflows.sequential_contracts import (
    BindingClaim, Closed, GenericDefinitionPin, GenericLimits, GenericRoleRequirement,
    GenericStartCommand, RequiredInput, Token,
)
from runtime.workflows.sequential_coding_evidence import _current_authority
from runtime.workflows.sequential_engine import _engine_rows, _identity
from runtime.workflows.sequential_history import (
    GenericStorageSettings, _ReadBudget, _bytes, _decode, _sql_budget, _generic_limits_value, _GENERIC_LIMITS_SCHEMA,
)
from runtime.workflows.sequential_views import GenericSettingsView, ProjectionRefusal


class StartRoleViewV2(Closed):
    role: str
    requirement: GenericRoleRequirement
    responsibilities: tuple[str, ...]


class StartDefinitionViewV2(Closed):
    selector: str
    pin: GenericDefinitionPin
    description: str
    roles: tuple[StartRoleViewV2, ...]
    required_inputs: tuple[RequiredInput, ...]
    definition_limits: Annotated[GenericLimits, WithJsonSchema(_GENERIC_LIMITS_SCHEMA)]
    registered: bool
    settings: GenericSettingsView | None
    admission: Literal['Disabled', 'Allowed']
    defaults: tuple[BindingClaim, ...]
    limits: Annotated[GenericLimits, WithJsonSchema(_GENERIC_LIMITS_SCHEMA)] | None
    unavailable_cause: Literal['sequential_storage_unavailable', 'sequential_definition_not_registered',
        'sequential_settings_absent', 'sequential_settings_version_conflict',
        'sequential_settings_disabled', 'sequential_assignments_unavailable'] | None

    @field_serializer('definition_limits', 'limits')
    def limits_value(self, value: GenericLimits | None) -> dict | None:
        return None if value is None else _generic_limits_value(value)


class StartContextV2(Closed):
    format: Literal['workflow-sequential-start-context@2'] = 'workflow-sequential-start-context@2'
    start_format: Literal['workflow-sequential-start@2'] = 'workflow-sequential-start@2'
    max_runs: Literal[8] = 8
    assignments: Literal['defaults-with-overrides'] = 'defaults-with-overrides'
    source_semantics: Literal['initiator-continues'] = 'initiator-continues'
    availability: Literal['admission-supported'] = 'admission-supported'
    unavailable_cause: None = None
    # Per-run bounds are pinned at admission; there is no cross-run numeric pool.
    remaining: None = None
    remaining_cause: Literal['native-per-run-bounds'] = 'native-per-run-bounds'
    delivered_notifications: tuple[Token, ...] = ()
    definitions: tuple[StartDefinitionViewV2, ...]


def read_start_source_v2(conn: sqlite3.Connection, org: Any, *, task_id: str,
                         principal: str, session_id: str, budget: _ReadBudget,
                         result_id: int | None = None) -> dict:
    """Own ordinary source or its own generic wait set; never @1 fallthrough."""
    from runtime.workflows.sequential_start import _ordinary_dependencies

    source, seen, key = None, set(), task_id
    while key is not None:
        budget.step()
        if key in seen or len(seen) >= 20:
            raise ProjectionRefusal('sequential_task_session_required', 403)
        seen.add(key)
        rows = _engine_rows(conn, 'tasks', budget, 'id=?', (key,))
        if not rows:
            raise ProjectionRefusal('task_not_found' if key == task_id else 'sequential_task_session_required',
                                    404 if key == task_id else 403)
        row = rows[0]
        owner = selected_owner(conn, key, org.slug)
        own_wait = ('seq_source_wait_set', _identity('wait-set', org.slug, task_id), None)
        if (owner != ('legacy', None, None) and not (key == task_id and owner == own_wait)
                or row['cancelled_at'] is not None
                or row['status'] in ('completed', 'failed', 'cancelled', 'superseded')):
            raise ProjectionRefusal('sequential_task_session_required', 403)
        if source is None:
            source = row
            if (row['task_type'] != 'task' or row['status'] != 'in_progress'
                    or (row['assigned_agent'], row['current_session_id']) != (principal, session_id)
                    or row['blocked_on_job_ids'] is not None or row['block_kind'] not in (None, 'delegated')
                    or row['zombie_flagged_at'] is not None or row['completed_at'] is not None
                    or org.teams.executable_manager_for_team(row['team']) != principal):
                raise ProjectionRefusal('sequential_task_session_required', 403)
            _ordinary_dependencies(row, budget)
        if row['team'] != source['team']:
            raise ProjectionRefusal('sequential_task_session_required', 403)
        key = row['parent_task_id']
    results = list(_bounded_rows(conn, 'task_results', ('id',), (('integer',),), budget,
            where='task_id=? AND agent=? AND session_id=?', args=(task_id, principal, session_id), maximum=1))
    if [row[0] for row in results] != ([] if result_id is None else [result_id]):
        raise ProjectionRefusal('sequential_task_session_required', 403)
    # The ancestry above already identifies the root. Acquire only its bounded
    # control cells, on the SAME snapshot/ledger, rather than re-reading every
    # task and the full native admission journal through the writer's helper.
    pause = list(_bounded_rows(conn, 'task_pause_controls', ('schema_version', 'generation', 'held'),
        (('integer',), ('integer',), ('integer',)), budget,
        where='root_task_id=?', args=(row['id'],), maximum=1))
    if pause:
        version, generation, held = pause[0][:3]
        if version != 1 or not 0 <= generation <= MAX_GENERATION or held not in (0, 1) or held != generation % 2:
            raise ProjectionRefusal('sequential_history_unverified')
        if held:
            raise ProjectionRefusal('sequential_source_paused')
    return source


def _definitions(conn: sqlite3.Connection, org: Any, catalog: tuple,
                 budget: _ReadBudget) -> StartContextV2:
    from runtime.workflows.sequential_builtin import inspect_definition
    from runtime.workflows.sequential_run_validation import _pins, _selected_layout

    present = conn.execute("SELECT 1 FROM sqlite_schema WHERE lower(name) GLOB 'workflow_seq_*' LIMIT 1").fetchone()
    metadata = _selected_layout(conn, budget) if present else None
    values = []
    for compiled in catalog:
        budget.step()
        cause, saved, registered = 'sequential_storage_unavailable', None, False
        if metadata is not None:
            definition, registered = inspect_definition(conn, compiled, budget)
            cause = 'sequential_definition_not_registered'
            if registered:
                rows = list(_bounded_rows(conn, 'workflow_seq_settings', ('id', 'definition_id'),
                    (('text',), ('text',)), budget,
                    where='org_slug=? AND definition_identity=? AND generation=(SELECT max(generation) '
                          'FROM workflow_seq_settings WHERE org_slug=? AND definition_identity=?)',
                    args=(org.slug, compiled.pin.identity, org.slug, compiled.pin.identity), maximum=1))
                cause = 'sequential_settings_absent'
                if rows:
                    stored, setting, snapshot = _pins(conn, metadata, org_slug=org.slug,
                        definition_id=rows[0][1], settings_id=rows[0][0], budget=budget)
                    cause = 'sequential_settings_version_conflict'
                    if stored == compiled and isinstance(snapshot, GenericStorageSettings):
                        saved = GenericSettingsView(id=setting['id'], generation=setting['generation'],
                            snapshot_digest=setting['snapshot_digest'], snapshot=snapshot)
                        cause = 'sequential_settings_disabled' if snapshot.admission == 'Disabled' else None
        values.append(StartDefinitionViewV2(selector=f'{compiled.pin.identity}@{compiled.pin.version}',
            pin=compiled.pin, description=compiled.definition.description,
            roles=tuple(StartRoleViewV2(role=role, requirement=requirement,
                responsibilities=tuple(text for node in compiled.definition.nodes if node.role == role
                                       for text in node.responsibilities))
                for role, requirement in compiled.definition.roles),
            required_inputs=compiled.definition.required_inputs, definition_limits=compiled.definition.limits,
            registered=registered, settings=saved, admission='Disabled' if saved is None else saved.snapshot.admission,
            defaults=() if saved is None else saved.snapshot.bindings,
            limits=None if saved is None else saved.snapshot.limits, unavailable_cause=cause))
    return StartContextV2(definitions=tuple(values))


async def read_start_context_v2(org: Any, *, task_id: str, principal: str, session_id: str,
                                selector: str | None, revalidate: Callable[[], None]) -> StartContextV2:
    """One finite ledger, SELECT-only snapshots; selection I/O outside leases."""
    from runtime.workflows import sequential_release as release

    budget = _ReadBudget()
    revalidate()
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        source = read_start_source_v2(conn, org, task_id=task_id, principal=principal,
                                     session_id=session_id, budget=budget)
    authority = org.workflow_authority
    capture = authority.capture_admission()
    budget.check()
    if not 0 < len(release.GENERIC_CATALOG) <= 16:
        raise ProjectionRefusal('sequential_definition_unavailable')
    for entry in release.GENERIC_CATALOG.values():
        budget.reserve(96 * entry.size + 4096)
    catalog = release.generic_start_catalog(selector)
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        initial = _definitions(conn, org, catalog, budget)
    checked = []
    for compiled, value in zip(catalog, initial.definitions, strict=True):
        if value.unavailable_cause is None:
            command = GenericStartCommand(format='workflow-sequential-start@2', operation_key='discovery',
                definition=value.pin, settings=dict(generation=value.settings.generation,
                    snapshot_digest=value.settings.snapshot_digest), request='Read-only discovery', inputs=[],
                role_overrides=[], step_overrides=[], lower_limits=value.limits)
            try:
                budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
                resolve_assignments(compiled, command, defaults=value.defaults, capture=capture,
                                    org_slug=org.slug, team=source['team'])
            except AssignmentRefusal:
                value = value.model_copy(update={'unavailable_cause': 'sequential_assignments_unavailable'})
        checked.append(value)
    async with asyncio.timeout(max(0, budget.deadline - time.monotonic())), \
            authority._async_writer_lock, org.teams_lock, org.db_lock:
        with ExitStack() as fences:
            for name in capture.profile_names:
                budget.step()
                fences.enter_context(authority._profile_coordinator.profile_read(name))
            fences.enter_context(authority._publisher_lock)
            fences.enter_context(org.db._lock)
            fences.enter_context(org.sessions.binding_lease(task_id, principal))
            conn = org.db._conn
            if conn.in_transaction:
                raise ProjectionRefusal('sequential_reader_transaction')
            conn.execute('BEGIN')
            try:
                with _sql_budget(conn, budget):
                    revalidate()
                    source = read_start_source_v2(conn, org, task_id=task_id, principal=principal,
                                                 session_id=session_id, budget=budget)
                    _current_authority(conn, authority, capture, budget)
                    snapshot = _decode(capture.ready.snapshot_bytes, budget)
                    if not any(a['name'] == principal and a['role'] == 'manager' and a['status'] == 'active'
                            and a['executor'] and a['team'] == source['team'] for a in snapshot['agents']):
                        raise ProjectionRefusal('sequential_authority_denied', 403)
                    if _definitions(conn, org, catalog, budget) != initial:
                        raise ProjectionRefusal('sequential_settings_conflict')
                    delivered = ()
                    if selected_owner(conn, task_id, org.slug)[0] == 'seq_source_wait_set':
                        from runtime.workflows.sequential_initiator import _audience, _page
                        _audience(conn, task_id, principal, session_id, budget, current=True)
                        page = _page(conn, task_id, session_id, budget)
                        delivered = tuple(item['notification_id'] for item in page['notifications'])
                    value = StartContextV2(definitions=tuple(checked), delivered_notifications=delivered)
                    raw = _bytes(value.model_dump(mode='json'))
                    budget.reserve(96 * len(raw) + 4096)
                    if len(raw) > 262144:
                        raise ProjectionRefusal('sequential_context_too_large')
                    _current_authority(conn, authority, capture, budget)
                    revalidate()
                    return value
            finally:
                conn.rollback()
