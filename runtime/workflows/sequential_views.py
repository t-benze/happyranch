"""U7a bounded Founder projections; SELECT-only, never admission or execution."""
from __future__ import annotations

import hashlib
import asyncio
import sqlite3
import time
from contextlib import ExitStack
from typing import Any, Callable, Literal

from runtime.workflows.sequential_contracts import (
    BindingClaim, Closed, Counters, DefinitionPin, InputValue, Limits, RoleRequirement,
    RequiredInput, SettingsPin, effective_limits,
)
from runtime.workflows.sequential_definition import compile_definition, parse_result, parse_start
from runtime.workflows.sequential_engine import SequentialRefusal, _bound_agents, _engine_rows
from runtime.workflows.sequential_history import (
    StorageSettings, _ReadBudget, _bytes, _decode, _parse, _sql_budget, read_sequential_history,
)
from runtime.workflows.sequential_repository_subject import read_repository_subject
from runtime.workflows.sequential_coding_evidence import _current_authority
from runtime.infrastructure.workflow_sequential_ownership import read_sequential_owner
from runtime.workflows.sequential_run_validation import _prior_state


class ProjectionRefusal(ValueError):
    def __init__(self, code: str, status: int = 409) -> None:
        self.code, self.status = code, status
        super().__init__(code)


class DefinitionView(Closed):
    id: str
    pin: DefinitionPin
    definition_json: str


class DefinitionList(Closed):
    format: Literal['workflow-sequential-definitions-view@1'] = 'workflow-sequential-definitions-view@1'
    storage: Literal['new-engine unavailable', 'storage readable']
    definitions: tuple[DefinitionView, ...]


class SettingsView(Closed):
    format: Literal['workflow-sequential-settings-view@1'] = 'workflow-sequential-settings-view@1'
    id: str
    generation: int
    snapshot_digest: str
    snapshot: StorageSettings


class TaskLink(Closed):
    task_id: str
    relationship: Literal['source', 'carrier', 'visit']
    visit_id: str | None = None


class RunView(Closed):
    id: str
    definition_id: str
    definition: DefinitionPin
    settings_id: str
    settings: SettingsPin
    state: Literal['active', 'held', 'completed', 'failed', 'cancelled']
    active_visit_id: str | None
    counters: Counters
    tasks: tuple[TaskLink, ...]


class RunList(Closed):
    format: Literal['workflow-sequential-runs-view@1'] = 'workflow-sequential-runs-view@1'
    storage: Literal['new-engine unavailable', 'storage readable']
    runs: tuple[RunView, ...]


class VisitView(Closed):
    id: str
    node_id: str
    ordinal: int
    epoch: int
    task_id: str
    principal: str
    state: str
    selected: bool
    outcome: str | None
    findings: tuple[str, ...]


class OutputView(Closed):
    id: str
    visit_id: str
    slot: str
    contract: str
    epoch: int
    content_digest: str
    content_utf8: str


class HoldView(Closed):
    visit_id: str | None
    cause: str
    next_owner: str
    action: str


class RunDetail(Closed):
    format: Literal['workflow-sequential-run-view@1'] = 'workflow-sequential-run-view@1'
    run: RunView
    request: str
    bindings: tuple[BindingClaim, ...]
    inputs: tuple[InputValue, ...]
    holds: tuple[HoldView, ...]
    limits: Limits
    effective_limits: Limits
    reducer_counters: Counters
    visits: tuple[VisitView, ...]
    outputs: tuple[OutputView, ...]


class TaskWorkflowView(Closed):
    format: Literal['workflow-sequential-task-link@1'] = 'workflow-sequential-task-link@1'
    task_id: str
    run: RunView | None


class StartRoleView(Closed):
    role: str
    requirement: RoleRequirement
    responsibilities: tuple[str, ...]


class StartDefinitionView(Closed):
    selector: str
    pin: DefinitionPin
    description: str
    roles: tuple[StartRoleView, ...]
    required_inputs: tuple[RequiredInput, ...]
    definition_limits: Limits
    registered: bool
    settings: SettingsView | None
    admission: Literal['Disabled', 'Allowed']
    defaults: tuple[BindingClaim, ...]
    limits: Limits | None
    unavailable_cause: Literal['sequential_storage_unavailable', 'sequential_definition_not_registered',
        'sequential_settings_absent', 'sequential_settings_disabled', 'sequential_host_unavailable'] | None


class StartContext(Closed):
    format: Literal['workflow-sequential-start-context@1'] = 'workflow-sequential-start-context@1'
    start_format: Literal['workflow-sequential-start@1'] = 'workflow-sequential-start@1'
    max_runs: Literal[1] = 1
    assignments: Literal['fixed'] = 'fixed'
    source_semantics: Literal['source-terminal'] = 'source-terminal'
    definitions: tuple[StartDefinitionView, ...]


def _start_definitions(conn: sqlite3.Connection, org: Any, catalog: tuple,
                       source: dict, capture: Any, budget: _ReadBudget) -> StartContext:
    from runtime.workflows.coding_authority_history import _bounded_rows
    from runtime.workflows.sequential_builtin import inspect_definition
    from runtime.workflows.sequential_run_validation import _pins, _selected_layout
    from runtime.workflows.sequential_start import _require_runtime

    present = conn.execute("SELECT 1 FROM sqlite_schema WHERE lower(name) GLOB 'workflow_seq_*' LIMIT 1").fetchone()
    metadata = _selected_layout(conn, budget) if present else None
    values = []
    for compiled in catalog:
        budget.step()
        cause = 'sequential_storage_unavailable'
        registered, saved = False, None
        if metadata is not None:
            definition, registered = inspect_definition(conn, compiled, budget)
            cause = 'sequential_definition_not_registered'
            if registered:
                # Indexed latest generation only; no whole-history/run scan or
                # caller-selected generation. _pins authenticates original bytes,
                # receipt and exact latest pin using the existing start reader.
                rows = list(_bounded_rows(conn, 'workflow_seq_settings', ('id',), (('text',),), budget,
                    where='org_slug=? AND definition_identity=? AND generation=(SELECT max(generation) '
                          'FROM workflow_seq_settings WHERE org_slug=? AND definition_identity=?)',
                    args=(org.slug, compiled.pin.identity, org.slug, compiled.pin.identity), maximum=1))
                cause = 'sequential_settings_absent'
                if rows:
                    stored, setting, snapshot = _pins(conn, metadata, org_slug=org.slug,
                        definition_id=definition['id'], settings_id=rows[0][0], budget=budget)
                    if stored != compiled:
                        raise ProjectionRefusal('sequential_definition_conflict')
                    budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
                    agents = _bound_agents(compiled, snapshot, capture)
                    if any(agent['team'] != source['team'] for agent in agents.values()):
                        raise ProjectionRefusal('sequential_authority_denied', 403)
                    saved = SettingsView(id=setting['id'], generation=setting['generation'],
                        snapshot_digest=setting['snapshot_digest'], snapshot=snapshot)
                    cause = 'sequential_settings_disabled' if snapshot.admission == 'Disabled' else None
                    if cause is None:
                        try:
                            _require_runtime(org)
                        except SequentialRefusal:
                            cause = 'sequential_host_unavailable'
        roles = tuple(StartRoleView(role=role, requirement=requirement,
            responsibilities=tuple(text for node in compiled.definition.nodes if node.role == role
                                   for text in node.responsibilities))
            for role, requirement in compiled.definition.roles)
        values.append(StartDefinitionView(selector=f'{compiled.pin.identity}@{compiled.pin.version}',
            pin=compiled.pin, description=compiled.definition.description, roles=roles,
            required_inputs=compiled.definition.required_inputs, definition_limits=compiled.definition.limits,
            registered=registered, settings=saved, admission='Disabled' if saved is None else saved.snapshot.admission,
            defaults=() if saved is None else saved.snapshot.bindings,
            limits=None if saved is None else saved.snapshot.limits, unavailable_cause=cause))
    return StartContext(definitions=tuple(values))


async def read_start_context(org: Any, *, task_id: str, principal: str, session_id: str,
                             selector: str | None, revalidate: Callable[[], None]) -> StartContext:
    """Own-manager context only; one read allowance, rollback-only snapshots.

    Current authority capture retains its existing cooperative file/lock I/O
    behavior. The original deadline is checked after capture and before return;
    it is not a hard OS-I/O or RSS guarantee. No durable lease is acquired.
    """
    from runtime.workflows import sequential_release as release
    from runtime.workflows.sequential_start import read_start_source

    budget = _ReadBudget()
    revalidate()
    # Classify the native audience before any settings or authority disclosure.
    with org.db.coherent_read_view() as conn, _sql_budget(conn, budget):
        read_start_source(conn, org, task_id=task_id, principal=principal, session_id=session_id, budget=budget)
    authority = org.workflow_authority
    capture = authority.capture_admission()
    budget.check()
    if not 0 < len(release.MANIFEST) <= 16:
        raise ProjectionRefusal('sequential_definition_unavailable')
    entries = tuple(entry for entry in release.MANIFEST.values()
                    if selector is None or selector == f'{entry.pin.identity}@{entry.pin.version}')
    if not entries:
        raise ProjectionRefusal('coding_fresh_start_retired' if selector == 'release/happyranch/coding@1'
                                else 'sequential_definition_unavailable')
    catalog = []
    for entry in entries:
        release.manifest_entry(entry.pin)
        budget.reserve(96 * entry.size + 4096)
        catalog.append(release.select_bundled_release(entry.pin))
        budget.check()
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
                    source = read_start_source(conn, org, task_id=task_id, principal=principal,
                                               session_id=session_id, budget=budget)
                    _current_authority(conn, authority, capture, budget)
                    snapshot = _decode(capture.ready.snapshot_bytes, budget)
                    managers = [a for a in snapshot['agents'] if a['name'] == principal]
                    if (len(managers) != 1 or managers[0]['role'] != 'manager'
                            or managers[0]['status'] != 'active' or not managers[0]['executor']
                            or managers[0]['team'] != source['team']):
                        raise ProjectionRefusal('sequential_authority_denied', 403)
                    value = _start_definitions(conn, org, tuple(catalog), source, capture, budget)
                    raw = _bytes(value.model_dump(mode='json'))
                    budget.reserve(96 * len(raw) + 4096)
                    if len(raw) > 262144:
                        raise ProjectionRefusal('sequential_context_too_large')
                    _current_authority(conn, authority, capture, budget)
                    revalidate()
                    return value
            finally:
                conn.rollback()


def _definition(row: dict, budget: _ReadBudget) -> DefinitionView:
    compiled = _parse(row['definition_bytes'], compile_definition, budget,
                      source_digest=row['source_digest'])
    return DefinitionView(id=row['id'], pin=compiled.pin,
                          definition_json=row['definition_bytes'].decode('utf8'))


def _run(conn: sqlite3.Connection, row: dict, capture: Any, budget: _ReadBudget
         ) -> tuple[RunView, list[dict], Any, Any]:
    definitions = _engine_rows(conn, 'workflow_seq_definitions', budget, 'id=?', (row['definition_id'],))
    compiled = _parse(definitions[0]['definition_bytes'], compile_definition, budget,
                      source_digest=definitions[0]['source_digest'])
    start = _parse(row['start_bytes'], parse_start, budget)
    # The original pins are authenticated by the complete oracle. Current role
    # eligibility and origin are rechecked separately; saved records grant none.
    budget.reserve(48 * len(capture.ready.snapshot_bytes) + 512)
    try:
        bound = _bound_agents(compiled, start, capture)
    except ValueError as exc:
        if str(exc) in ('workflow_seq_role_unavailable', 'workflow_seq_role_ineligible'):
            raise ProjectionRefusal('sequential_authority_denied', 403) from None
        raise
    subject = read_repository_subject(conn, row, compiled, budget)
    if subject is not None:
        for agent in bound.values():
            budget.step()
            origin = agent['repos'].get(subject.subject.repo_key)
            if not isinstance(origin, str) or hashlib.sha256(origin.encode('utf8')).hexdigest() != subject.subject.origin_digest:
                raise ProjectionRefusal('sequential_authority_denied', 403)
    visits = _engine_rows(conn, 'workflow_seq_visits', budget, 'run_id=?', (row['id'],))
    budget.work(len(visits) * max(1, len(visits).bit_length()))
    visits.sort(key=lambda item: item['ordinal'])
    links = [TaskLink(task_id=row['source_task_id'], relationship='source'),
             TaskLink(task_id=row['carrier_task_id'], relationship='carrier')]
    links.extend(TaskLink(task_id=item['task_id'], relationship='visit', visit_id=item['id']) for item in visits)
    return RunView(id=row['id'], definition_id=row['definition_id'], definition=compiled.pin,
        settings_id=row['settings_id'], settings=start.settings, state=row['state'], active_visit_id=row['active_visit_id'],
        counters=Counters(epoch=row['epoch'], content_returns=row['content_returns'],
            visits=row['visit_count'], events=row['event_count']), tasks=tuple(links)), visits, start, compiled


def _project(conn: sqlite3.Connection, org: Any, kind: str, selected: str | None,
             capture: Any, budget: _ReadBudget, history: Any) -> Closed:
    # Absent-task classification must precede any ownership/schema inspection.
    if kind == 'task' and conn.execute('SELECT 1 FROM tasks WHERE id=?', (selected,)).fetchone() is None:
        raise ProjectionRefusal('task_not_found', 404)
    if not history.layout.sequential:
        if kind == 'task':
            # The caller already proved native existence, current authority and
            # complete compatible history. Only this positive absence is empty.
            return TaskWorkflowView(task_id=selected, run=None)
        if kind == 'definitions':
            return DefinitionList(storage=history.status, definitions=())
        if kind == 'runs':
            return RunList(storage=history.status, runs=())
        raise ProjectionRefusal('sequential_storage_unavailable')
    if kind in ('definitions', 'definition'):
        rows = _engine_rows(conn, 'workflow_seq_definitions', budget,
            'identity=?' if kind == 'definitions' else 'id=?',
            ('release/happyranch/coding' if kind == 'definitions' else selected,))
        views = tuple(_definition(row, budget) for row in rows)
        if kind == 'definition':
            if not views:
                raise ProjectionRefusal('sequential_definition_not_found', 404)
            return views[0]
        return DefinitionList(storage=history.status, definitions=views)
    if kind == 'settings':
        rows = _engine_rows(conn, 'workflow_seq_settings', budget, 'org_slug=? AND definition_identity=?',
                            (org.slug, selected))
        if not rows:
            raise ProjectionRefusal('sequential_settings_not_found', 404)
        row = max(rows, key=lambda item: item['generation'])
        snapshot = StorageSettings.model_validate(_decode(row['snapshot_bytes'], budget))
        return SettingsView(id=row['id'], generation=row['generation'],
                            snapshot_digest=row['snapshot_digest'], snapshot=snapshot)
    if kind == 'task':
        read_sequential_owner(conn, task_id=selected, org_slug=org.slug)
        rows = _engine_rows(conn, 'workflow_seq_runs', budget,
            'source_task_id=? OR carrier_task_id=? OR id IN (SELECT run_id FROM workflow_seq_visits WHERE task_id=?)',
            (selected, selected, selected))
        if not rows:
            return TaskWorkflowView(task_id=selected, run=None)
    else:
        rows = _engine_rows(conn, 'workflow_seq_runs', budget,
                            'org_slug=?' if kind == 'runs' else 'org_slug=? AND id=?',
                            (org.slug,) if kind == 'runs' else (org.slug, selected))
    if kind != 'runs' and len(rows) != 1:
        raise ProjectionRefusal('sequential_run_not_found', 404)
    values = [_run(conn, row, capture, budget) for row in rows]
    if kind == 'runs':
        return RunList(storage=history.status, runs=tuple(value[0] for value in values))
    run, visits, start, compiled = values[0]
    if kind == 'task':
        return TaskWorkflowView(task_id=selected, run=run)
    visit_views = []
    for visit in visits:
        budget.step()
        result = None if visit['result_bytes'] is None else _parse(visit['result_bytes'], parse_result, budget)
        # Successful Final findings are arbitrary native-bearing text. Keep the
        # browser projection closed without changing the stored envelope.
        visit_views.append(VisitView(**{key: visit[key] for key in
            ('id', 'node_id', 'ordinal', 'epoch', 'task_id', 'principal', 'state')},
            selected=bool(visit['selected']), outcome=None if result is None else result.outcome,
            findings=() if result is None or any(output.contract == 'guarded-repository-merge@1'
                for output in result.outputs) else tuple(finding.text for finding in result.findings)))
    outputs = _engine_rows(conn, 'workflow_seq_outputs', budget, 'run_id=?', (run.id,))
    # Only the existing Founder bearer reader reaches this projection. Task
    # sessions cannot use it to bypass the addressed-input disclosure owner.
    # R-M3-6: complete replay authenticates Final's original observation, but
    # only this closed summary crosses the browser boundary. Keep its stored
    # digest and all other output contracts unchanged; never copy native proof.
    output_views = tuple(OutputView(**{key: item[key] for key in
        ('id', 'visit_id', 'slot', 'contract', 'epoch', 'content_digest')},
        content_utf8='{"format":"workflow-sequential-coding-final-summary@1","outcome":"merged"}'
        if item['contract'] == 'guarded-repository-merge@1'
        else item['content_bytes'].decode('utf8')) for item in outputs)
    holds = []
    for row in _engine_rows(conn, 'workflow_seq_evidence', budget, 'run_id=? AND kind=?', (run.id, 'observation')):
        value = _decode(row['evidence_bytes'], budget)
        holds.append(HoldView(visit_id=row['visit_id'], cause=value['cause'],
                              next_owner=value['next_owner'], action=value['action']))
    original = _parse(rows[0]['original_limits_bytes'], lambda raw: Limits.model_validate(_decode(raw, budget)), budget)
    events = _engine_rows(conn, 'workflow_seq_events', budget, 'run_id=? AND kind=?', (run.id, 'transition'))
    budget.work(len(events) * max(1, len(events).bit_length()))
    events.sort(key=lambda item: item['sequence'])
    # Complete replay above authenticates these immutable before/after records.
    # The engine's counter owner ignores admission/cancel/return events; the
    # existing RunView.counters.events remains the database event_count.
    counters, _ = _prior_state(rows[0], compiled, start, original, visits, events, {}, budget)
    budget.reserve(512)
    return RunDetail(run=run, request=start.request, bindings=start.bindings, inputs=start.inputs, holds=tuple(holds),
        limits=original, effective_limits=effective_limits(compiled.definition.limits, original, start.lower_limits),
        reducer_counters=counters,
        visits=tuple(visit_views), outputs=output_views)


async def read_projection(org: Any, *, kind: str, selected: str | None = None) -> Closed:
    """Fresh captured authority, original read deadline, no durable lease/DML."""
    deadline = time.monotonic() + 5.0
    budget = _ReadBudget(deadline=deadline)
    authority = org.workflow_authority
    capture = authority.capture_admission()
    async with authority._async_writer_lock, org.teams_lock, org.db_lock:
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
                # Match native task API ordering even if the ownership is bad.
                if kind == 'task' and conn.execute('SELECT 1 FROM tasks WHERE id=?', (selected,)).fetchone() is None:
                    raise ProjectionRefusal('task_not_found', 404)
                with _sql_budget(conn, budget):
                    _current_authority(conn, authority, capture, budget)
                history = read_sequential_history(conn, expected_org_slug=org.slug, deadline=deadline)
                # Complete reader owns/clears its hook; restore the projection hook.
                with _sql_budget(conn, budget):
                    value = _project(conn, org, kind, selected, capture, budget, history)
                    budget.reserve(96 * len(_bytes(value.model_dump(mode='json'))) + 4096)
                    _current_authority(conn, authority, capture, budget)
                    return value
            except SequentialRefusal as exc:
                raise ProjectionRefusal(exc.code, 403 if exc.code in
                    ('role_unavailable', 'role_ineligible', 'sequential_authority_denied') else 409) from None
            finally:
                conn.rollback()
