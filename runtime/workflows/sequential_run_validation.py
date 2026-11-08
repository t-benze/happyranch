"""Admission-only, selected pre-COMMIT validation for the inert U3b writer.

The complete reader remains the offline oracle, including older-row tampering.
This incremental validator never acquires earlier result/output payloads.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Iterator

from runtime.infrastructure.workflow_schema import _candidate_reserve, _execute_ddl, _schema_snapshot, _structure_cells
from runtime.infrastructure.workflow_sequential_schema import CANONICAL_WORKFLOW_SEQUENTIAL_DDL
from runtime.infrastructure.workflow_sequential_ownership import (
    SEQUENTIAL_OWNERSHIP_DDL, read_sequential_owner, sequential_binding_view_ddl,
)
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows
from runtime.workflows.sequential_contracts import Limits
from runtime.workflows.sequential_definition import CompiledDefinition, parse_start, validate_start
from runtime.workflows.sequential_history import (
    StorageSettings, _ReadBudget, _auxiliary, _bytes, _decode, _definitions,
    _event_payload, _hashed, _native_contract, _native_rows, _parse, _record,
    _require, _result, _selection, _sql_budget,
)


def _selected_structure(conn: sqlite3.Connection, budget: _Budget) -> tuple[tuple, tuple]:
    """Complete bounded observation, including unrelated examined object kinds.

    No DATA, physical integrity, FK data or complete-owner reader is entered.
    The independent old/new canonical comparator remains exact.
    """
    objects = _structure_cells(conn, "SELECT rowid,type,name,tbl_name,sql FROM sqlite_schema WHERE lower(name) GLOB 'workflow_seq_*' OR lower(tbl_name) GLOB 'workflow_seq_*' OR name IN ('workflow_task_ownership','workflow_task_owner_bindings') OR tbl_name IN ('workflow_task_ownership','workflow_task_owner_bindings')", ('type', 'name', 'tbl_name', 'sql'), ('rowid',), budget)
    _candidate_reserve(budget, 128 * len(objects))
    for _ in range(len(objects) * max(1, len(objects).bit_length())):
        budget.step()
    objects.sort(key=lambda row: row[:3])
    selected = list((row for row in objects if any((name.casefold().startswith('workflow_') for name in row[1:3]))))
    selected_names = set((row[1] for row in selected))
    normalized = tuple(((kind, name, table, None if sql is None else ' '.join(sql.split())) for kind, name, table, sql in selected))
    tables, indexes = ([], [])
    for kind, name, _, _ in objects:
        if kind != 'table' or name not in selected_names:
            continue
        columns = tuple(_structure_cells(conn, 'SELECT * FROM pragma_table_xinfo(?)', ('cid', 'name', 'type', 'notnull', 'dflt_value', 'pk', 'hidden'), ('cid',), budget, (name,)))
        foreign = tuple(_structure_cells(conn, 'SELECT * FROM pragma_foreign_key_list(?)', ('id', 'seq', 'table', 'from', 'to', 'on_update', 'on_delete', 'match'), ('id', 'seq'), budget, (name,)))
        table_indexes = []
        for index in _structure_cells(conn, 'SELECT * FROM pragma_index_list(?)', ('seq', 'name', 'unique', 'origin', 'partial'), ('seq',), budget, (name,)):
            detail = tuple(_structure_cells(conn, 'SELECT * FROM pragma_index_xinfo(?)', ('seqno', 'cid', 'name', 'desc', 'coll', 'key'), ('seqno',), budget, (index[1],)))
            table_indexes.append((name, index, detail))
        tables.append((name, columns, foreign))
        indexes.extend(table_indexes)
    return ((normalized, tuple(tables), tuple(indexes)), tuple(objects))


def _selected_rows(conn: sqlite3.Connection, table: str, metadata: tuple[tuple, ...],
                   budget: _ReadBudget, *, columns: tuple[str, ...],
                   where: str, args: tuple) -> Iterator[dict]:
    # Complete schema was independently matched; names/types are trusted only
    # after that match. Reuse pre-fetch typeof/length gating, never SELECT *.
    # These exact columns were acquired and matched inside this snapshot by
    # _selected_layout. Reacquisition would charge the same metadata rows twice.
    metadata = tuple(row for row in metadata if row[1] in columns)
    _require(tuple(row[1] for row in metadata) == columns, 'selected_columns_invalid')
    names = tuple(row[1] for row in metadata)
    kinds = {'TEXT': 'text', 'INTEGER': 'integer', 'BLOB': 'blob'}
    allowed = tuple((kinds[row[2]],) + (() if row[3] else ('null',)) for row in metadata)
    for row in _bounded_rows(conn, table, names, allowed, budget, where=where, args=args):
        values = row[:len(names)]
        budget.reserve(512 + 128 * len(names) + 2 * sum(
            len(value.encode('utf8')) if type(value) is str else len(value)
            for value in values if type(value) in (str, bytes)))
        yield dict(zip(names, values, strict=True))


def _selected_layout(conn: sqlite3.Connection, budget: _ReadBudget) -> dict[str, tuple[tuple, ...]]:
    _require(not any(str(value).casefold().startswith('workflow_')
                     or str(value).casefold() in ('tasks', 'task_results')
                     for row in _structure_cells(conn, 'SELECT rowid,name,tbl_name FROM sqlite_temp_schema',
                         ('name', 'tbl_name'), ('rowid',), budget) for value in row), 'temporary_shadow_refused')
    actual = _selected_structure(conn, budget)[0]
    # Named-table probe only; mirror _read_layout's document/coding letters.
    names = {row[0] for row in _bounded_rows(conn, 'sqlite_schema', ('name',), (('text',),), budget,
        where="type='table' AND (name IN ('workflow_document_revision_operations','workflow_author_handoffs',"
              "'workflow_submission_schema_versions','workflow_draft_adapter_versions') OR lower(name) GLOB 'workflow_coding_*')")}
    coding = any(str(name).casefold().startswith('workflow_coding_') for name in names)
    document = ('I' if 'workflow_document_revision_operations' in names else
                'H' if 'workflow_author_handoffs' in names else
                'G' if 'workflow_submission_schema_versions' in names else
                'E' if 'workflow_draft_adapter_versions' in names else 'F')
    if document == 'I' and coding:
        document = 'J'
    expected = sqlite3.connect(':memory:')
    try:
        with _sql_budget(expected, budget):
            _execute_ddl(expected, CANONICAL_WORKFLOW_SEQUENTIAL_DDL)
            expected.execute(SEQUENTIAL_OWNERSHIP_DDL)
            expected.execute(sequential_binding_view_ddl(document, coding_extension=coding))
            reference = _selected_structure(expected, budget)[0]
            budget.work(sum(len(part) for part in actual) + sum(len(part) for part in reference))
            _require(actual == reference, 'selected_layout_mismatch')
    finally:
        expected.close()
    budget.reserve(128 * len(actual[1]))
    metadata = {table: columns for table, columns, _ in actual[1]}
    _native_contract(conn, budget)
    return metadata


def _types(data: dict[str, list[dict]], budget: _ReadBudget) -> None:
    for rows in data.values():
        for row in rows:
            budget.step()
            for name, value in row.items():
                if name in ('version', 'generation', 'epoch', 'content_returns', 'visit_count',
                            'event_count', 'cas', 'ordinal', 'sequence', 'selected'):
                    _require(type(value) is int, 'integer_storage_required')
                elif name.endswith('result_id'):
                    _require(value is None or (type(value) is int and value > 0), 'result_identity_invalid')
                elif name.endswith(('_id', '_principal')) or name in ('id', 'org_slug', 'principal', 'operation_key', 'start_operation_key'):
                    _require(value is None or (type(value) is str and bool(value) and '\x00' not in value), 'identity_invalid')


def _selected_settings(data: dict[str, list[dict]], definitions: dict[str, CompiledDefinition],
                       org_slug: str, budget: _ReadBudget) -> tuple[dict[str, dict], dict[str, StorageSettings]]:
    rows = {row['id']: row for row in data['workflow_seq_settings']}
    snapshots = {}
    for key, row in rows.items():
        budget.step()
        _require(row['org_slug'] == org_slug and row['definition_id'] in definitions, 'settings_owner_invalid')
        snapshot = StorageSettings.model_validate(_decode(_hashed(row['snapshot_bytes'], row['snapshot_digest']), budget))
        definition = definitions[row['definition_id']]
        bindings = {item.role: item.principal for item in snapshot.bindings}
        _require(snapshot.definition == definition.pin and row['definition_identity'] == definition.pin.identity
                 and len(bindings) == len(snapshot.bindings)
                 and bindings.keys() == dict(definition.definition.roles).keys(), 'settings_pin_conflict')
        for role, requirement in definition.definition.roles:
            _require(all(bindings[role] != bindings[other] for other in requirement.independent_of),
                     'settings_independence_conflict')
        snapshots[key] = snapshot
    seen = set()
    for row in data['workflow_seq_settings_operations']:
        budget.step()
        target = rows.get(row['settings_id'])
        _require(target is not None and target['id'] not in seen and row['org_slug'] == org_slug,
                 'settings_operation_conflict')
        request = _record(row['request_bytes'], row['request_digest'], {
            'format', 'operation_key', 'principal', 'settings_id', 'expected_generation', 'snapshot_digest'}, budget)
        receipt = _record(row['receipt_bytes'], row['receipt_digest'], {
            'format', 'operation_id', 'settings_id', 'generation', 'snapshot_digest'}, budget)
        _require(_bytes(request) == _bytes(dict(format='workflow-sequential-settings-operation-storage@1',
                 operation_key=row['operation_key'], principal=row['principal'], settings_id=target['id'],
                 expected_generation=target['generation'] - 1, snapshot_digest=target['snapshot_digest'])),
                 'settings_request_conflict')
        _require(_bytes(receipt) == _bytes(dict(format='workflow-sequential-settings-receipt-storage@1',
                 operation_id=row['id'], settings_id=target['id'], generation=target['generation'],
                 snapshot_digest=target['snapshot_digest'])), 'settings_receipt_conflict')
        seen.add(target['id'])
    _require(seen == rows.keys(), 'settings_operation_missing')
    return rows, snapshots


def _read_selected(conn: sqlite3.Connection, metadata: dict, table: str, budget: _ReadBudget,
                   where: str, args: tuple, *, omit: tuple[str, ...] = ()) -> list[dict]:
    columns = tuple(row[1] for row in metadata[table] if row[1] not in omit)
    return list(_selected_rows(conn, table, metadata[table], budget, columns=columns, where=where, args=args))


def _pins(conn: sqlite3.Connection, metadata: dict, *, org_slug: str, definition_id: str,
          settings_id: str, budget: _ReadBudget) -> tuple[CompiledDefinition, dict, StorageSettings]:
    data = {}
    for table, where, args in (
        ('workflow_seq_definitions', 'id=?', (definition_id,)),
        ('workflow_seq_settings', 'id=?', (settings_id,)),
        ('workflow_seq_settings_operations', 'settings_id=?', (settings_id,)),
    ):
        data[table] = _read_selected(conn, metadata, table, budget, where, args)
        _require(len(data[table]) == 1, 'selected_pin_missing')
    _types(data, budget)
    definitions = _definitions(data, budget)
    settings, snapshots = _selected_settings(data, definitions, org_slug, budget)
    setting = settings[settings_id]
    _require(setting['definition_id'] == definition_id, 'run_pin_invalid')
    newer = list(_bounded_rows(conn, 'workflow_seq_settings', ('generation',), (('integer',),), budget,
        where='org_slug=? AND definition_identity=? AND generation>?',
        args=(org_slug, setting['definition_identity'], setting['generation']), maximum=1))
    _require(not newer, 'settings_generation_stale')
    return definitions[definition_id], setting, snapshots[settings_id]


def _ownership(conn: sqlite3.Connection, metadata: dict, *, org_slug: str, run: dict, visit: dict,
               budget: _ReadBudget) -> None:
    visits = {visit['id']: visit}
    for task, kind, owner, state in (
        (run['source_task_id'], 'seq_source_waiting', run['id'], run['state']),
        (run['carrier_task_id'], 'seq_carrier', run['id'], run['state']),
        (visit['task_id'], 'seq_visit', visit['id'], visit['state']),
    ):
        marks = _read_selected(conn, metadata, 'workflow_task_ownership', budget, 'task_id=?', (task,))
        _types({'ownership': marks}, budget)
        _require(marks == [dict(task_id=task, org_slug=org_slug, version=2, kind=kind, owner_id=owner)],
                 'ownership_relation_conflict')
        _require(read_sequential_owner(conn, task_id=task, org_slug=org_slug) == (kind, owner, state),
                 'ownership_relation_conflict')
        native_rows = _native_rows(conn, 'tasks',
            ('id', 'parent_task_id', 'assigned_agent', 'current_session_id'),
            (('text',), ('text', 'null'), ('text', 'null'), ('text', 'null')), task, budget)
        _require(len(native_rows) == 1, 'ownership_reference_missing')
        native = native_rows[0]
        # Parallel to validate_sequential_ownership's v2 native-row branch.
        if kind == 'seq_source_waiting':
            valid = task == run['source_task_id'] and native[2] == run['source_principal']
        elif kind == 'seq_carrier':
            valid = (task == run['carrier_task_id'] and native[1] == run['source_task_id']
                     and native[3] is None)
        else:
            visit = visits.get(owner)
            valid = (visit is not None and visit['task_id'] == task and visit['run_id'] == run['id']
                     and native[1] == run['carrier_task_id'] and native[2] == visit['principal'])
        if not valid:
            raise ValueError('workflow_seq_ownership_native_conflict')
        if kind != 'seq_source_waiting':
            queued = _native_rows(conn, 'tasks', ('status', 'current_session_id', 'active_chain', 'active_fanout'),
                (('text',), ('null',), ('null',), ('null',)), task, budget)
            _require(queued == [('pending', None, None, None)], 'admission_task_not_pristine')


def validate_selected_run_uncommitted(conn: sqlite3.Connection, *, org_slug: str, run_id: str,
                                      scope: str, evidence_ids: tuple[str, ...] = ()) -> _ReadBudget:
    """Validate admission writes in the caller transaction; return charged work.

    Other writer scopes deliberately refuse. The returned original budget is
    diagnostic accounting, not authority, behavioral proof or a new allowance.
    """
    _require(conn.in_transaction, 'validation_requires_transaction')
    _require(scope == 'admission', 'validation_scope_unavailable')
    _require(type(org_slug) is str and bool(org_slug) and type(run_id) is str and bool(run_id), 'identity_invalid')
    _require(type(evidence_ids) is tuple and len(evidence_ids) <= 8192
             and len(set(evidence_ids)) == len(evidence_ids)
             and all(type(key) is str and key and '\x00' not in key for key in evidence_ids), 'evidence_identity_invalid')
    budget = _ReadBudget()
    with _sql_budget(conn, budget), _schema_snapshot(conn):
        metadata = _selected_layout(conn, budget)
        data = {'workflow_seq_runs': _read_selected(conn, metadata, 'workflow_seq_runs', budget, 'id=?', (run_id,))}
        _require(len(data['workflow_seq_runs']) == 1, 'run_missing')
        run = data['workflow_seq_runs'][0]
        _require(run['org_slug'] == org_slug, 'run_pin_invalid')
        compiled, settings, snapshot = _pins(conn, metadata, org_slug=org_slug,
            definition_id=run['definition_id'], settings_id=run['settings_id'], budget=budget)
        for table, where, args, omit in (
            ('workflow_seq_schema_versions', 'version=?', (1,), ()),
            ('workflow_seq_visits', 'run_id=?', (run_id,), ('result_bytes', 'verified_checks_bytes')),
            ('workflow_seq_events', 'run_id=?', (run_id,), ()),
            ('workflow_seq_outbox', 'event_id=?', (run['last_event_id'],), ()),
        ):
            data[table] = _read_selected(conn, metadata, table, budget, where, args, omit=omit)
        data['workflow_seq_evidence'] = (_read_selected(conn, metadata, 'workflow_seq_evidence', budget,
            'id IN (' + ','.join('?' for _ in evidence_ids) + ')', evidence_ids) if evidence_ids else [])
        _require({row['id'] for row in data['workflow_seq_evidence']} == set(evidence_ids), 'evidence_missing')
        _types(data, budget)
        _require(data['workflow_seq_schema_versions'] == [{'version': 1}], 'schema_marker_invalid')
        _require(len(data['workflow_seq_visits']) == len(data['workflow_seq_events']) == len(data['workflow_seq_outbox']) == 1,
                 'admission_closure_invalid')
        visit, event = data['workflow_seq_visits'][0], data['workflow_seq_events'][0]
        start = _parse(_hashed(run['start_bytes'], run['start_digest']), parse_start, budget)
        original = Limits.model_validate(_decode(run['original_limits_bytes'], budget))
        limits = validate_start(start, compiled, original_limits=original)
        _require(start.operation_key == run['start_operation_key']
                 and start.settings.generation == settings['generation']
                 and start.settings.snapshot_digest == settings['snapshot_digest']
                 and start.bindings == snapshot.bindings and snapshot.admission == 'Allowed'
                 and all(getattr(start.lower_limits, key) <= getattr(snapshot.limits, key)
                         for key in Limits.model_fields), 'admission_pin_conflict')
        _result(conn, run['source_result_id'], run['source_task_id'], run['source_principal'], run['source_session_id'], budget)
        node = next(node for node in compiled.definition.nodes if node.id == compiled.definition.entry_node)
        bindings = {binding.role: binding.principal for binding in start.bindings}
        _require((visit['node_id'], visit['ordinal'], visit['epoch'], visit['predecessor_event_id'], visit['principal'],
                  visit['session_id'], visit['result_id'], visit['result_digest'], visit['state'], visit['selected']) ==
                 (node.id, 1, 1, None, bindings[node.role], None, None, None, 'queued', 1)
                 and not _selection(visit, budget), 'entry_visit_invalid')
        _require((event['kind'], event['sequence'], event['prior_digest'], event['operation_key'],
                  event['source_visit_id'], event['source_result_id']) ==
                 ('admitted', 1, '0' * 64, start.operation_key, None, None), 'admitted_event_invalid')
        _event_payload(event, budget)
        _require(run['state'] == 'active' and run['cas'] == run['event_count'] - 1 == 0
                 and run['last_event_id'] == event['id'] and run['active_visit_id'] == visit['id']
                 and (run['epoch'], run['content_returns'], run['visit_count'], run['event_count']) == (1, 0, 1, 1)
                 and run['visit_count'] <= limits.max_visits and run['event_count'] <= limits.max_events
                 and run['epoch'] <= limits.max_content_epochs and run['content_returns'] <= limits.content_returns,
                 'run_history_conflict')
        _auxiliary(conn, data, {run_id: run}, {visit['id']: visit}, {event['id']: event}, budget)
        _ownership(conn, metadata, org_slug=org_slug, run=run, visit=visit, budget=budget)
        budget.check()
    return budget
