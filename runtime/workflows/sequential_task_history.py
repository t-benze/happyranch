"""Scoped sequential integrity primitives, never current authority.

Task projections keep THR-404 Option B's pinned settings contract. Explicit
write roots additionally authenticate the full selected settings chains; writer
wiring and retained-family certification belong to their respective owners.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from runtime.workflows.coding_authority_history import _Budget

from runtime.infrastructure.workflow_schema import _structure_cells
from runtime.infrastructure.workflow_sequential_ownership import (
    read_sequential_owner, validate_sequential_ownership,
)
from runtime.infrastructure.workflow_sequential_schema import sequential_tables
from runtime.workflows.sequential_history import (
    SequentialHistory, _ReadBudget, _auxiliary, _decode, _definitions, _hashed, _native_contract,
    _outputs, _read_layout, _require, _run_history, _settings, _sql_budget,
)
from runtime.workflows.sequential_run_validation import (
    _generic_admission_history, _read_selected, _selected_rows, _selected_settings,
)


def _in(column: str, values: set[str] | set[int]) -> tuple[str, tuple[str | int, ...]]:
    # Only literal schema column names reach here; identities remain parameters.
    return column + ' IN (' + ','.join('?' for _ in values) + ')', tuple(sorted(values))


def _foreign_keys(conn: sqlite3.Connection, table: str, rows: list[dict], budget: _ReadBudget, *,
                  schema_budget: _ReadBudget) -> None:
    """Apply the declared FK predicates to selected rows (SQLite MATCH SIMPLE)."""
    keys: dict[int, list[tuple]] = {}
    with _sql_budget(conn, schema_budget):
        for key in _structure_cells(conn, 'SELECT * FROM pragma_foreign_key_list(?)',
                ('id', 'seq', 'table', 'from', 'to', 'on_update', 'on_delete', 'match'),
                ('id', 'seq'), schema_budget, (table,)):
            keys.setdefault(key[0], []).append(key)
    for row in rows:
        for parts in keys.values():
            budget.step()
            values = tuple(row[part[3]] for part in parts)
            if any(value is None for value in values):
                continue
            # Exact layout recognition authenticated these identifiers.
            where = ' AND '.join('"' + part[4] + '"=?' for part in parts)
            _require(conn.execute('SELECT 1 FROM "' + parts[0][2] + '" WHERE ' + where, values).fetchone()
                     is not None, 'foreign_key_invalid')


def _data_types(data: dict[str, list[dict]], budget: _ReadBudget) -> None:
    # Preserve complete-reader scalar rules, including @2's nullable epoch.
    for rows in data.values():
        for row in rows:
            budget.step()
            for name, value in row.items():
                if name in ('version', 'generation', 'epoch', 'content_returns', 'visit_count',
                            'event_count', 'cas', 'ordinal', 'sequence', 'selected'):
                    _require(type(value) is int or (row.get('engine_version') == 2
                        and name in ('epoch', 'content_returns') and value is None), 'integer_storage_required')
                elif name.endswith('result_id'):
                    _require(value is None or (type(value) is int and value > 0), 'result_identity_invalid')
                elif name.endswith(('_id', '_principal')) or name in (
                        'id', 'org_slug', 'principal', 'operation_key', 'start_operation_key'):
                    _require(value is None or (type(value) is str and bool(value) and '\x00' not in value),
                             'identity_invalid')


def _graph(conn: sqlite3.Connection, metadata: dict, task_id: str | None, budget: _ReadBudget, *,
           run_roots: frozenset[str] = frozenset(),
           source_roots: frozenset[str] = frozenset()) -> dict[str, list[dict]]:
    def read(table: str, where: str, args: tuple) -> list[dict]:
        return _read_selected(conn, metadata, table, budget, where, args)

    # Separate indexed probes avoid a whole-run scan for an ordinary task.
    run_ids = set(run_roots)
    sources = set(source_roots)
    if task_id is not None:
        run_ids.update(row['id'] for column in ('source_task_id', 'carrier_task_id')
            for row in read('workflow_seq_runs', column + '=?', (task_id,)))
        run_ids.update(row['run_id'] for row in read('workflow_seq_visits', 'task_id=?', (task_id,)))
        sources.add(task_id)
    waits: set[str] = set()
    runs: dict[str, dict] = {}
    members: dict[str, dict] = {}
    if 'workflow_seq_source_wait_sets' in metadata:
        waits.update(row['id'] for row in read('workflow_seq_source_wait_sets', *_in('source_task_id', sources)))
    # A member needs the entire source/wait set, including earlier batches and
    # native decision receipts; a wait-set mark must never select one member.
    while True:
        before = (len(run_ids), len(sources), len(waits))
        for row in read('workflow_seq_runs', *_in('id', run_ids)):
            runs[row['id']] = row
            sources.add(row['source_task_id'])
        for row in read('workflow_seq_runs', *_in('source_task_id', sources)):
            runs[row['id']] = row
            run_ids.add(row['id'])
        if 'workflow_seq_source_wait_sets' in metadata:
            for column, identities in (('run_id', run_ids), ('source_task_id', sources), ('wait_set_id', waits)):
                for row in read('workflow_seq_source_memberships', *_in(column, identities)):
                    members[row['run_id']] = row
                    run_ids.add(row['run_id'])
                    sources.add(row['source_task_id'])
                    waits.add(row['wait_set_id'])
            for row in read('workflow_seq_source_wait_sets', *_in('source_task_id', sources)):
                waits.add(row['id'])
                sources.add(row['source_task_id'])
        budget.step()
        if before == (len(run_ids), len(sources), len(waits)):
            break
    _require(runs.keys() == run_ids, 'orphan_history')
    data = {table: [] for table in sequential_tables(2 if 'workflow_seq_source_wait_sets' in metadata else 1)}
    data['workflow_seq_runs'] = list(runs.values())
    data['workflow_seq_schema_versions'] = read('workflow_seq_schema_versions', '1', ())
    if not runs and not waits:
        return data  # Indexed negative lookup; never scan receipt/retained tables.
    for table, column in (('workflow_seq_definitions', 'definition_id'), ('workflow_seq_settings', 'settings_id')):
        data[table] = read(table, *_in('id', {row[column] for row in runs.values()}))
    data['workflow_seq_settings_operations'] = read('workflow_seq_settings_operations',
        *_in('settings_id', {row['settings_id'] for row in runs.values()}))
    for table in data:
        if table in ('workflow_seq_runs', 'workflow_seq_schema_versions', 'workflow_seq_definitions',
                     'workflow_seq_settings', 'workflow_seq_settings_operations'):
            continue
        if table == 'workflow_seq_visits':
            predicate, args = _in('run_id', run_ids)
            parents, carriers = _in('parent_task_id', {row['carrier_task_id'] for row in runs.values()})
            data[table] = read(table, predicate + ' OR task_id IN (SELECT id FROM tasks WHERE ' + parents + ')',
                               args + carriers)
            continue
        columns = {row[1] for row in metadata[table]}
        predicates, args = [], ()
        edges = [('run_id', run_ids), ('initiator_task_id', sources)]
        if table != 'workflow_seq_visits':
            edges += [('visit_id', {row['id'] for row in data['workflow_seq_visits']}),
                      ('source_visit_id', {row['id'] for row in data['workflow_seq_visits']})]
        if table != 'workflow_seq_events':
            edges += [('event_id', {row['id'] for row in data['workflow_seq_events']})]
        for column, identities in edges:
            if column in columns and identities:
                predicate, values = _in(column, identities)
                predicates.append(predicate)
                args += values
        if predicates:
            data[table] = read(table, ' OR '.join(predicates), args)
    if 'workflow_seq_source_wait_sets' in metadata:
        data['workflow_seq_source_wait_sets'] = read('workflow_seq_source_wait_sets', *_in('id', waits))
        data['workflow_seq_source_memberships'] = list(members.values())
    return data


def _native_metadata(conn: sqlite3.Connection, data: dict[str, list[dict]], budget: _ReadBudget, *,
                     schema_budget: _ReadBudget,
                     retained_budget: _Budget | None = None) -> None:
    """Retain old Coding discrimination on the native rows in this graph.

    Per-run replay owns sequential/Coding evidence. Retained family bindings
    cannot share these tasks, but reserved native metadata still needs its
    original discriminator and narrower 16 MiB allowance.
    """
    from runtime.infrastructure.workflow_coding_schema import (
        CODING_NAMESPACE_COLUMNS, CODING_PREFIXES, CODING_VALUES,
        _coding_column_masks, _coding_scalar, _refuse_coding_metadata,
    )
    from runtime.workflows.coding_authority_history import _bounded_rows
    legacy = _Budget() if retained_budget is None else retained_budget
    opening = legacy.consumed, legacy.accepted_rows, legacy.traversed
    cells = sum(len(row) for rows in data.values() for row in rows)
    budget.work(cells)
    budget.reserve(128 * cells)
    tasks = {value for rows in data.values() for row in rows for key, value in row.items()
             if key.endswith('task_id') and value is not None}
    results = {value for rows in data.values() for row in rows for key, value in row.items()
               if key.endswith('result_id') and value is not None}
    generic_runs = {row['id'] for row in data['workflow_seq_runs'] if row.get('engine_version', 1) == 2}
    for row in data['workflow_seq_evidence']:
        budget.step()
        if row['run_id'] not in generic_runs or row['kind'] not in ('terminal', 'outer'):
            continue
        # Replay authenticated these proofs in this snapshot. Pending/rejected
        # callbacks can be trusted before any scalar result_id is populated.
        # Source-outer IDs are also retained (and already match the run column).
        proof = _decode(_hashed(row['evidence_bytes'], row['evidence_digest']), budget)
        for reference in (proof, proof.get('callback')):
            if reference is not None and reference.get('result_id') is not None:
                results.add(reference['result_id'])
    try:
        for table, identities in (('tasks', tasks), ('task_results', results)):
            with _sql_budget(conn, schema_budget):
                columns = [row[1] for row in _structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)',
                    ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), schema_budget, (table,))]
            scalar = [column for column in columns if column in CODING_PREFIXES
                      or column in CODING_VALUES or column in CODING_NAMESPACE_COLUMNS]
            structured = [column for column in columns
                if (column.endswith(('_bytes', '_json')) or column in ('payload', 'author_principal', 'published_by'))
                and column not in ('submission_bytes', 'candidate_bytes')]
            selected = tuple(dict.fromkeys(('id', *scalar, *structured)))
            predicate, args = _in('id', identities)
            for row in _bounded_rows(conn, table, selected, _coding_column_masks(table, selected),
                                     legacy, where=predicate, args=args):
                for column, raw in zip(selected[1:], row[1:len(selected)], strict=True):
                    legacy.step()
                    if column in scalar:
                        _require(not _coding_scalar(column, raw), 'native_coding_metadata_conflict')
                    else:
                        decision = table == 'task_results' and column == 'decision_json'
                        _refuse_coding_metadata(raw, location=table + '.' + column, budget=legacy,
                            sequential=data if decision else None, result_id=row[0] if decision else None)
    finally:
        budget.consumed += legacy.consumed - opening[0]
        budget.accepted_rows += legacy.accepted_rows - opening[1]
        budget.traversed += legacy.traversed - opening[2]
    # Apply C's original cumulative ceilings after charging retained work.
    from runtime.workflows.coding_authority_history import _CALL_ROW_LIMIT
    from runtime.workflows._read_exhaustion import check
    check(budget.accepted_rows <= _CALL_ROW_LIMIT, lambda ok: _require(ok, 'read_rows_exhausted'),
          budget, 'rows', legacy.accepted_rows - opening[1],
          _CALL_ROW_LIMIT - budget.accepted_rows + legacy.accepted_rows - opening[1])
    budget.reserve(0)


def _replay(conn: sqlite3.Connection, metadata: dict, data: dict[str, list[dict]], *,
            expected_org_slug: str, budget: _ReadBudget, schema_budget: _ReadBudget,
            full_settings: bool = False,
            retained_budget: _Budget | None = None) -> dict:
    """Full selected replay shared with the original task projection."""
    _data_types(data, budget)
    for table, rows in data.items():
        _foreign_keys(conn, table, rows, budget, schema_budget=schema_budget)
    definitions = _definitions(data, budget)
    settings, snapshots = (_settings if full_settings else _selected_settings)(
        data, definitions, expected_org_slug, budget)
    runs = {row['id']: row for row in data['workflow_seq_runs']}
    visits = {row['id']: row for row in data['workflow_seq_visits']}
    events = {row['id']: row for row in data['workflow_seq_events']}
    _require(all(row['run_id'] in runs for table, rows in data.items()
                 for row in rows if 'run_id' in row), 'orphan_history')
    outputs = _outputs(data, visits, runs, definitions, budget)
    if runs:
        native_tasks = {row['source_task_id'] for row in runs.values()} | {
            row['carrier_task_id'] for row in runs.values()} | {row['task_id'] for row in visits.values()}
        predicate, args = _in('owner_id', set(runs) | set(visits) | {
            row['id'] for row in data.get('workflow_seq_source_wait_sets', [])})
        marks = _read_selected(conn, metadata, 'workflow_task_ownership', budget,
            'version IN (2,3) AND (' + predicate + ')', args)
        _require(all(row['task_id'] in native_tasks for row in marks), 'ownership_relation_conflict')
    for run in runs.values():
        budget.step()
        setting = settings.get(run['settings_id'])
        _require(run['org_slug'] == expected_org_slug and run['definition_id'] in definitions
            and setting is not None and setting['definition_id'] == run['definition_id'], 'run_pin_invalid')
        if run.get('engine_version', 1) == 2:
            _generic_admission_history(conn, org_slug=expected_org_slug, run=run,
                compiled=definitions[run['definition_id']], settings=setting, snapshot=snapshots[setting['id']],
                data=data, budget=budget)
        else:
            rv = [row for row in visits.values() if row['run_id'] == run['id']]
            re = [row for row in events.values() if row['run_id'] == run['id']]
            budget.work(len(visits) + len(events) + len(rv) * max(1, len(rv).bit_length())
                + len(re) * max(1, len(re).bit_length()))
            _run_history(conn, run, definitions[run['definition_id']], setting, snapshots[setting['id']],
                sorted(rv, key=lambda row: row['ordinal']), sorted(re, key=lambda row: row['sequence']),
                outputs, budget, [row for row in data['workflow_seq_evidence']
                                 if row['run_id'] == run['id'] and row['kind'] == 'observation'])
    if 'workflow_seq_source_wait_sets' in metadata:
        for table in ('workflow_seq_assignments', 'workflow_seq_controls', 'workflow_seq_node_counters'):
            _require(all(row['run_id'] in runs and runs[row['run_id']]['engine_version'] == 2
                         for row in data[table]), 'control_owner_conflict')
        from runtime.workflows.sequential_initiator import validate_initiator_history
        validate_initiator_history(conn, data, runs, visits, events, definitions, budget, expected_org_slug)
    _auxiliary(conn, data, runs, visits, events, budget, definitions)
    validate_sequential_ownership(conn, org_slug=expected_org_slug, budget=budget,
        runs={key: row for key, row in runs.items() if row.get('engine_version', 1) == 1},
        visits={key: row for key, row in visits.items() if row.get('engine_version', 1) == 1})
    _native_metadata(conn, data, budget, schema_budget=schema_budget, retained_budget=retained_budget)
    return definitions


def read_task_history(conn: sqlite3.Connection, *, expected_org_slug: str,
                      task_id: str, deadline: float) -> SequentialHistory:
    """Complete schema + selected data, under the projection's existing locks.

    Full per-run replay includes @1/Coding evidence and original native results;
    selected ownership checks reject cross-family bindings. No retained family
    has a sequential run FK. Unrelated retained rows and unpinned settings
    generations are deliberately outside this read contract.
    """
    _require(conn.in_transaction, 'task_history_requires_snapshot')
    observed = _ReadBudget(deadline=deadline)
    reference = _ReadBudget(deadline=deadline)
    budget = _ReadBudget(deadline=deadline)
    layout, metadata = _read_layout(conn, observed, reference, budget)
    if not layout.sequential:
        return SequentialHistory(layout, 'new-engine unavailable', (), ())
    with _sql_budget(conn, observed):
        _native_contract(conn, observed)
    with _sql_budget(conn, budget):
        _require(layout.ownership == 2, 'companion_ownership_required')
        # Even an orphan selected mark must refuse rather than become empty.
        read_sequential_owner(conn, task_id=task_id, org_slug=expected_org_slug)
        data = _graph(conn, metadata, task_id, budget)
        _require([tuple(row.values()) for row in data['workflow_seq_schema_versions']] == [(layout.schema_version,)],
                 'schema_marker_invalid')
        if not data['workflow_seq_runs']:
            _require(not data.get('workflow_seq_source_wait_sets'), 'initiator_membership_missing')
            budget.check()
            return SequentialHistory(layout, 'storage readable', (), ())
        definitions = _replay(conn, metadata, data, expected_org_slug=expected_org_slug,
                              budget=budget, schema_budget=observed)
        runs = {row['id']: row for row in data['workflow_seq_runs']}
        budget.check()
        return SequentialHistory(layout, 'storage readable', tuple(sorted(definitions)), tuple(sorted(runs)))


@dataclass(frozen=True)
class ScopedSequentialHistory:
    """Snapshot-local sequential certificate and its original charged C ledger.

    No retained-family inventory is certified. Do not cache across mutations or
    transactions, or substitute this for whole-org installation/load validation.
    """

    history: SequentialHistory
    data: dict[str, list[dict]]
    metadata: dict
    budget: _ReadBudget


def _roots(values: frozenset[str], budget: _ReadBudget) -> frozenset[str]:
    for value in values:
        budget.step()
        _require(type(value) is str and bool(value) and '\x00' not in value
                 and len(value.encode('utf8')) <= 1048576, 'identity_invalid')
        budget.reserve(512 + 2 * len(value.encode('utf8')))
    return frozenset(values)


def _settings_chain(conn: sqlite3.Connection, metadata: dict, data: dict[str, list[dict]], *,
                    org_slug: str, definition_ids: frozenset[str], settings_ids: frozenset[str],
                    budget: _ReadBudget) -> None:
    """Assemble all generations/receipts for every selected definition identity."""
    def selected(table: str, column: str, ids: set[str] | frozenset[str]) -> list[dict]:
        return _read_selected(conn, metadata, table, budget, *_in(column, ids))

    definitions = {row['id']: row for row in data['workflow_seq_definitions']}
    settings = {row['id']: row for row in data['workflow_seq_settings']}
    definitions.update((row['id'], row) for row in selected('workflow_seq_definitions', 'id', definition_ids))
    settings.update((row['id'], row) for row in selected('workflow_seq_settings', 'id', settings_ids))
    _require(definition_ids <= definitions.keys() and settings_ids <= settings.keys(), 'selected_pin_missing')
    identities = {row['identity'] for row in definitions.values()} | {
        row['definition_identity'] for row in settings.values()}
    predicate, args = _in('definition_identity', identities)
    settings.update((row['id'], row) for row in _read_selected(conn, metadata, 'workflow_seq_settings', budget,
        'org_slug=? AND (' + predicate + ')', (org_slug, *args)))
    definitions.update((row['id'], row) for row in selected('workflow_seq_definitions', 'id', {
        row['definition_id'] for row in settings.values()} - definitions.keys()))
    data['workflow_seq_definitions'] = list(definitions.values())
    data['workflow_seq_settings'] = list(settings.values())
    data['workflow_seq_settings_operations'] = selected('workflow_seq_settings_operations', 'settings_id', set(settings))


def read_write_history(conn: sqlite3.Connection, *, expected_org_slug: str, deadline: float,
                       run_ids: frozenset[str] = frozenset(), source_ids: frozenset[str] = frozenset(),
                       definition_ids: frozenset[str] = frozenset(), settings_ids: frozenset[str] = frozenset(),
                       retained_budget: _Budget | None = None) -> ScopedSequentialHistory:
    """Full replay of explicit run/source closure and complete settings chains.

    The caller owns the existing transaction, locks and absolute deadline. This
    acquires none, commits nothing, and performs no current-authority checks.
    A settings/definition-only root may select zero runs. H is applied separately
    at the caller's original capacity seam, using the returned ledger.
    """
    _require(conn.in_transaction, 'task_history_requires_snapshot')
    _require(type(expected_org_slug) is str and bool(expected_org_slug), 'org_required')
    budget = _ReadBudget(deadline=deadline)
    roots = [_roots(values, budget) for values in (run_ids, source_ids, definition_ids, settings_ids)]
    run_ids, source_ids, definition_ids, settings_ids = roots
    observed, reference = _ReadBudget(deadline=deadline), _ReadBudget(deadline=deadline)
    layout, metadata = _read_layout(conn, observed, reference, budget)
    _require(layout.sequential and layout.ownership == 2, 'companion_ownership_required')
    # As in the complete reader, native ABI/FK metadata consumes the existing
    # observed-schema ledger A, leaving native data/replay and H on C.
    with _sql_budget(conn, observed):
        _native_contract(conn, observed)
    with _sql_budget(conn, budget):
        for source in source_ids:
            read_sequential_owner(conn, task_id=source, org_slug=expected_org_slug)
        data = _graph(conn, metadata, None, budget, run_roots=run_ids, source_roots=source_ids)
        _require([tuple(row.values()) for row in data['workflow_seq_schema_versions']] == [(layout.schema_version,)],
                 'schema_marker_invalid')
        _require(data['workflow_seq_runs'] or not data.get('workflow_seq_source_wait_sets'),
                 'initiator_membership_missing')
        _settings_chain(conn, metadata, data, org_slug=expected_org_slug,
                        definition_ids=definition_ids, settings_ids=settings_ids, budget=budget)
        definitions = _replay(conn, metadata, data, expected_org_slug=expected_org_slug,
                             budget=budget, schema_budget=observed,
                             full_settings=True, retained_budget=retained_budget)
    budget.check()
    history = SequentialHistory(layout, 'storage readable', tuple(sorted(definitions)),
                                tuple(sorted(row['id'] for row in data['workflow_seq_runs'])))
    return ScopedSequentialHistory(history, data, metadata, budget)


def read_cancellation_headroom(conn: sqlite3.Connection, *, expected_org_slug: str,
                               metadata: dict, budget: _ReadBudget) -> dict[str, list[dict]]:
    """Select scalar H inputs for ALL unfinished @2 runs, including cancelling.

    Metadata must come from layout recognition in this same transaction. This
    does not replay unrelated payloads or reserve capacity. The caller combines
    any hypothetical admissions, then calls _reserve_generic_cancellation once
    on its already charged ledger. No fresh allowance or count-based shortcut.
    """
    _require(conn.in_transaction, 'task_history_requires_snapshot')
    _require(type(expected_org_slug) is str and bool(expected_org_slug), 'org_required')
    budget.check()
    data: dict[str, list[dict]] = {'workflow_seq_runs': [], 'workflow_seq_visits': []}
    if 'workflow_seq_source_wait_sets' not in metadata:
        return data

    def read(table: str, names: set[str], where: str, args: tuple) -> list[dict]:
        columns = tuple(row[1] for row in metadata[table] if row[1] in names)
        return list(_selected_rows(conn, table, metadata[table], budget, columns=columns, where=where, args=args))

    with _sql_budget(conn, budget):
        runs = read('workflow_seq_runs', {'id', 'org_slug', 'engine_version', 'state', 'source_task_id',
            'source_principal', 'source_session_id', 'carrier_task_id', 'active_visit_id'},
            "engine_version=2 AND state IN ('running','held')", ())
        visits = read('workflow_seq_visits', {'id', 'run_id', 'engine_version', 'task_id', 'principal',
            'session_id', 'selected'}, *_in('id', {row['active_visit_id'] for row in runs
                                                 if row['active_visit_id'] is not None}))
        data.update(workflow_seq_runs=runs, workflow_seq_visits=visits)
        _data_types(data, budget)
        for row in (*runs, *visits):
            for key in ('id', 'source_task_id', 'source_principal', 'source_session_id',
                        'carrier_task_id', 'task_id', 'principal', 'session_id'):
                if key in row and row[key] is not None:
                    _require(type(row[key]) is str and bool(row[key]) and '\x00' not in row[key], 'identity_invalid')
        by_id = {row['id']: row for row in visits}
        for run in runs:
            budget.step()
            _require(run['org_slug'] == expected_org_slug, 'run_pin_invalid')
            visit = by_id.get(run['active_visit_id'])
            _require((run['state'] == 'held' and run['active_visit_id'] is None) or
                (run['state'] == 'running' and visit is not None and visit['run_id'] == run['id']
                 and visit['engine_version'] == 2 and visit['selected'] == 1), 'headroom_visit_conflict')
    budget.check()
    return data
