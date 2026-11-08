"""Selected pre-COMMIT validation for inert U3b/U3c writers.

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
from runtime.workflows.sequential_contracts import Counters, Limits, NextEdge, OutputRef, TerminalEdge
from runtime.workflows.sequential_definition import CompiledDefinition, parse_result, parse_start, parse_transition, validate_start
from runtime.workflows.sequential_reducer import ActiveVisit, AuthenticatedResult, RunState, reduce_result
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
                                      scope: str, evidence_ids: tuple[str, ...] = (),
                                      visit_id: str | None = None, event_id: str | None = None) -> _ReadBudget:
    """Validate selected writes in the caller transaction; return charged work.

    Replay selectors name the original accepted visit or transition event.
    The returned original budget is
    diagnostic accounting, not authority, behavioral proof or a new allowance.
    """
    _require(conn.in_transaction, 'validation_requires_transaction')
    if scope != 'admission':
        return _validate_lifecycle(conn, org_slug=org_slug, run_id=run_id, scope=scope, evidence_ids=evidence_ids,
                                   visit_id=visit_id, event_id=event_id)
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


def _metadata_outputs(conn: sqlite3.Connection, metadata: dict, visits: list[dict],
                      budget: _ReadBudget) -> dict[str, tuple[OutputRef, ...]]:
    outputs = {}
    for visit in visits:
        rows = _read_selected(conn, metadata, 'workflow_seq_outputs', budget, 'visit_id=?', (visit['id'],),
                              omit=('content_bytes', 'recipients_bytes'))
        refs = []
        for row in rows:
            _require((row['run_id'], row['task_id'], row['session_id'], row['result_id'], row['epoch']) ==
                     (visit['run_id'], visit['task_id'], visit['session_id'], visit['result_id'], visit['epoch']),
                     'output_result_conflict')
            refs.append(OutputRef(slot=row['slot'], contract=row['contract'], epoch=row['epoch'],
                producer_visit=visit['id'], task_id=row['task_id'], session_id=row['session_id'],
                result_id=row['result_id'], digest=row['content_digest']))
        outputs[visit['id']] = tuple(refs)
    return outputs


def _prior_state(run: dict, compiled: CompiledDefinition, start: object, original: Limits,
                 visits: list[dict], events: list[dict], outputs: dict[str, tuple[OutputRef, ...]],
                 budget: _ReadBudget) -> tuple[Counters, tuple[OutputRef, ...]]:
    """Reconstruct only counters and selected refs, without old result/content bytes.

    Only previously validated immutable transitions are trusted by this incremental
    reader. Complete authentication of older history remains the offline oracle.
    Findings and receipts do not affect the persisted reducer decision fields.
    """
    counters = Counters(epoch=1, content_returns=0, visits=1, events=0)
    selections: tuple[OutputRef, ...] = ()
    for event in events:
        if event['kind'] != 'transition':
            continue
        record = _parse(_hashed(event['event_bytes'], event['event_digest']), parse_transition, budget)
        _require(record.run_id == run['id'] and record.definition_digest == compiled.pin.definition_digest
                 and record.before == counters and record.prior_cas == event['sequence'] - 2
                 and record.prior_event_digest == event['prior_digest'], 'transition_anchor_conflict')
        refs = outputs.get(record.source_visit, ())
        if refs:
            slots = {ref.slot for ref in refs}
            selections = tuple(ref for ref in selections if ref.slot not in slots) + refs
        retired = set(record.invalidated)
        selections = tuple(ref for ref in selections if ref not in retired)
        counters = record.after
    return counters, selections


def _accepted(conn: sqlite3.Connection, metadata: dict, run: dict, visit: dict,
              compiled: CompiledDefinition, budget: _ReadBudget, *, content: bool) -> AuthenticatedResult:
    from runtime.workflows.sequential_engine import check_output_size, _check_output_length

    rows = _read_selected(conn, metadata, 'workflow_seq_visits', budget, 'id=?', (visit['id'],))
    _require(len(rows) == 1, 'visit_missing')
    row = rows[0]
    node = next(node for node in compiled.definition.nodes if node.id == row['node_id'])
    if content:
        # Check the raw length before the generic 1 MiB cell guard can refuse
        # payload acquisition with a less specific error on a document output.
        cursor = conn.execute('SELECT typeof(content_bytes),length(CAST(content_bytes AS BLOB)) '
                              'FROM workflow_seq_outputs WHERE visit_id=?', (row['id'],))
        try:
            for size in cursor:
                budget.step()
                budget.candidate()
                budget.reserve(32)
                _require(size[0] == 'blob' and type(size[1]) is int, 'output_content_invalid')
                _check_output_length(node.output_contract.type, size[1])
        finally:
            cursor.close()
    outputs = _read_selected(conn, metadata, 'workflow_seq_outputs', budget, 'visit_id=?', (row['id'],),
                             omit=() if content else ('content_bytes', 'recipients_bytes'))
    if content:
        # Repeat the raw gate before any output decode, including _outputs below.
        for output in outputs:
            check_output_size(node.output_contract.type, output['content_bytes'])
    _result(conn, row['result_id'], row['task_id'], row['principal'], row['session_id'], budget)
    envelope = _parse(_hashed(row['result_bytes'], row['result_digest']), parse_result, budget)
    checks = _decode(row['verified_checks_bytes'], budget)
    _require(set(checks) == {'checks'}, 'verified_checks_invalid')
    authenticated = AuthenticatedResult(task_id=row['task_id'], session_id=row['session_id'],
        result_id=row['result_id'], envelope=envelope, verified_checks=checks['checks'])
    _require((envelope.run_id, envelope.visit_id, envelope.definition_digest, envelope.input_selection_digest) ==
             (run['id'], row['id'], compiled.pin.definition_digest, row['input_selection_digest']),
             'result_envelope_conflict')
    if content:
        from runtime.workflows.sequential_history import _outputs
        actual = _outputs({'workflow_seq_outputs': outputs}, {row['id']: row}, {run['id']: run},
                          {run['definition_id']: compiled}, budget).get(row['id'], [])
    else:
        actual = [dict(slot=o['slot'], contract=o['contract'], epoch=o['epoch'], producer_visit=row['id'],
            task_id=o['task_id'], session_id=o['session_id'], result_id=o['result_id'], digest=o['content_digest'])
            for o in outputs]
    _require(sorted(actual, key=lambda item: item['slot']) ==
             sorted((ref.model_dump() for ref in envelope.outputs), key=lambda item: item['slot']),
             'output_envelope_conflict')
    _require(len(outputs) <= 1 and all(output['slot'] == node.output_contract.slot
             and output['contract'] == node.output_contract.type for output in outputs), 'output_contract_conflict')
    return authenticated


def _validate_lifecycle(conn: sqlite3.Connection, *, org_slug: str, run_id: str, scope: str,
                        evidence_ids: tuple[str, ...], visit_id: str | None, event_id: str | None) -> _ReadBudget:
    _require(scope in ('session', 'accept', 'transition', 'return', 'cancel', 'evidence'),
             'validation_scope_unavailable')
    _require(type(org_slug) is str and bool(org_slug) and type(run_id) is str and bool(run_id), 'identity_invalid')
    _require(type(evidence_ids) is tuple and len(evidence_ids) <= 8192
             and len(set(evidence_ids)) == len(evidence_ids)
             and all(type(key) is str and key and '\x00' not in key for key in evidence_ids), 'evidence_identity_invalid')
    _require(scope == 'evidence' or not evidence_ids, 'evidence_scope_invalid')
    if scope == 'evidence':
        from runtime.workflows.sequential_engine import SequentialRefusal
        if len(evidence_ids) > 2:
            raise SequentialRefusal('sequential_evidence_limit_exceeded')
    budget = _ReadBudget()
    with _sql_budget(conn, budget), _schema_snapshot(conn):
        metadata = _selected_layout(conn, budget)
        runs = _read_selected(conn, metadata, 'workflow_seq_runs', budget, 'id=?', (run_id,))
        _require(len(runs) == 1 and runs[0]['org_slug'] == org_slug, 'run_missing')
        run = runs[0]
        compiled, settings, snapshot = _pins(conn, metadata, org_slug=org_slug,
            definition_id=run['definition_id'], settings_id=run['settings_id'], budget=budget)
        start = _parse(_hashed(run['start_bytes'], run['start_digest']), parse_start, budget)
        original = Limits.model_validate(_decode(run['original_limits_bytes'], budget))
        limits = validate_start(start, compiled, original_limits=original)
        _require(start.operation_key == run['start_operation_key'] and start.bindings == snapshot.bindings
                 and start.settings.generation == settings['generation']
                 and start.settings.snapshot_digest == settings['snapshot_digest'] and snapshot.admission == 'Allowed',
                 'admission_pin_conflict')
        visits = _read_selected(conn, metadata, 'workflow_seq_visits', budget, 'run_id=?', (run_id,),
                                omit=('result_bytes', 'verified_checks_bytes'))
        events = _read_selected(conn, metadata, 'workflow_seq_events', budget, 'run_id=?', (run_id,))
        visits.sort(key=lambda row: row['ordinal'])
        events.sort(key=lambda row: row['sequence'])
        _types({'runs': runs, 'visits': visits, 'events': events}, budget)
        _require([row['ordinal'] for row in visits] == list(range(1, len(visits) + 1))
                 and 0 < len(visits) <= limits.max_visits
                 and [row['sequence'] for row in events] == list(range(1, len(events) + 1))
                 and 0 < len(events) <= limits.max_events, 'run_order_invalid')
        by_id = {row['id']: row for row in visits}
        selected = [row['id'] for row in visits if row['selected']]
        _require(selected == ([] if run['active_visit_id'] is None else [run['active_visit_id']])
                 and run['cas'] == run['event_count'] - 1 == len(events) - 1
                 and run['visit_count'] == len(visits) and run['last_event_id'] == events[-1]['id'],
                 'run_history_conflict')
        prior = '0' * 64
        outbox = []
        for event in events:
            _require(event['run_id'] == run_id and event['prior_digest'] == prior, 'event_chain_invalid')
            _hashed(event['event_bytes'], event['event_digest'])
            if event['kind'] != 'transition':
                _event_payload(event, budget)
            prior = event['event_digest']
            outbox.extend(_read_selected(conn, metadata, 'workflow_seq_outbox', budget, 'event_id=?', (event['id'],)))
        _require(events[0]['kind'] == 'admitted', 'admitted_event_invalid')
        outputs = _metadata_outputs(conn, metadata, visits, budget)
        counters, _ = _prior_state(run, compiled, start, original, visits, events, outputs, budget)
        _require((run['epoch'], run['content_returns'], run['visit_count']) ==
                 (counters.epoch, counters.content_returns, counters.visits), 'run_counter_conflict')
        bindings = {binding.role: binding.principal for binding in start.bindings}
        nodes = {node.id: node for node in compiled.definition.nodes}
        first = visits[0]
        _require(first['node_id'] == compiled.definition.entry_node and first['predecessor_event_id'] is None
                 and first['epoch'] == 1 and not _selection(first, budget), 'entry_visit_invalid')
        for visit in visits:
            _require(visit['node_id'] in nodes and visit['principal'] == bindings[nodes[visit['node_id']].role],
                     'visit_binding_conflict')
        active = by_id.get(run['active_visit_id'])
        if run['state'] in ('active', 'held'):
            _require(active is not None and active['epoch'] == counters.epoch
                     and active['state'] in (('held',) if run['state'] == 'held' else ('queued', 'running', 'waiting')),
                     'active_visit_invalid')
        else:
            _require(active is None, 'active_pointer_conflict')
        touched = [active] if active is not None else []
        if scope == 'session':
            _require(active is not None and active['state'] == 'running' and active['session_id'] is not None
                     and active['result_id'] is None, 'session_binding_conflict')
        elif scope == 'accept':
            accepted_visit = by_id.get(visit_id) if visit_id is not None else active
            _require(accepted_visit is not None and accepted_visit['result_id'] is not None,
                     'accept_visit_conflict')
            _accepted(conn, metadata, run, accepted_visit, compiled, budget, content=True)
            touched.append(accepted_visit)
        elif scope == 'transition':
            event = next((row for row in events if row['id'] == event_id), None) if event_id else events[-1]
            _require(event is not None and event['kind'] == 'transition' and event['source_visit_id'] in by_id, 'transition_missing')
            source = by_id[event['source_visit_id']]
            accepted = _accepted(conn, metadata, run, source, compiled, budget, content=False)
            before, selections = _prior_state(run, compiled, start, original, visits, events[:event['sequence'] - 1], outputs, budget)
            current = ActiveVisit(node=source['node_id'], visit_id=source['id'], task_id=source['task_id'],
                session_id=source['session_id'], epoch=source['epoch'], input_selection_digest=source['input_selection_digest'])
            decision = reduce_result(compiled, RunState(run_id=run_id, definition_digest=compiled.pin.definition_digest,
                original_limits=original, lower_limits=start.lower_limits, counters=before, active_visit=current,
                selections=selections, findings=(), receipts=()), accepted)
            record = _parse(event['event_bytes'], parse_transition, budget)
            _require((record.source_result, record.result_digest, record.node, record.outcome, record.before,
                      record.after, record.invalidated, record.edge, record.output_digest) ==
                     (accepted.result_id, decision.result_digest, source['node_id'], decision.outcome,
                      decision.before, decision.after, decision.invalidated, decision.edge,
                      accepted.envelope.outputs[0].digest if accepted.envelope.outputs else None),
                     'transition_reducer_conflict')
            if isinstance(decision.edge, NextEdge):
                successor = record.successor
                target = by_id.get(successor.visit_id) if successor is not None else None
                _require(successor is not None and target is not None and decision.activation is not None
                         and (target['id'], target['task_id'], target['node_id'], target['predecessor_event_id']) ==
                         (successor.visit_id, successor.task_id, successor.node, event['id'])
                         and _selection(target, budget) == decision.activation.inputs,
                         'successor_conflict')
                touched.append(target)
                if event is events[-1]:
                    _require(active is target and source['state'] == 'completed'
                             and source['selected'] == 0 and run['state'] == 'active',
                         'successor_conflict')
            else:
                expected = ('completed' if isinstance(decision.edge, TerminalEdge) else
                            'failed' if decision.edge.action == 'fail' else 'held')
                if event is events[-1]:
                    _require(run['state'] == source['state'] == expected
                             and source['selected'] == int(expected == 'held'), 'transition_state_conflict')
            touched.append(source)
        elif scope == 'return':
            _require(run['state'] == 'completed' and events[-1]['kind'] == 'returned'
                     and len(events) >= 3 and events[-2]['kind'] == 'transition'
                     and isinstance(_parse(events[-2]['event_bytes'], parse_transition, budget).edge, TerminalEdge),
                     'return_conflict')
        elif scope == 'cancel':
            _require(run['state'] == 'cancelled' and events[-1]['kind'] == 'cancelled'
                     and visits[-1]['state'] == 'cancelled' and not visits[-1]['selected'], 'cancellation_conflict')
            touched.append(visits[-1])
        if scope == 'evidence':
            from runtime.workflows.sequential_engine import EVIDENCE_CONTENT_LIMIT, SequentialRefusal
            # Fetch only length by the declared primary key, before content is
            # acquired, digested or decoded by the unchanged _auxiliary.
            for key in evidence_ids:
                budget.step()
                size = conn.execute('SELECT typeof(evidence_bytes),length(CAST(evidence_bytes AS BLOB)) '
                                    'FROM workflow_seq_evidence WHERE id=?', (key,)).fetchone()
                _require(size is not None, 'evidence_missing')
                budget.candidate()
                budget.reserve(32)
                _require(size[0] == 'blob' and type(size[1]) is int, 'evidence_content_invalid')
                if size[1] > EVIDENCE_CONTENT_LIMIT:
                    raise SequentialRefusal('sequential_evidence_limit_exceeded')
        evidence = (_read_selected(conn, metadata, 'workflow_seq_evidence', budget,
            'id IN (' + ','.join('?' for _ in evidence_ids) + ')', evidence_ids) if evidence_ids else [])
        _require({row['id'] for row in evidence} == set(evidence_ids), 'evidence_missing')
        _auxiliary(conn, {'workflow_seq_evidence': evidence, 'workflow_seq_outbox': outbox},
                   {run_id: run}, by_id, {row['id']: row for row in events}, budget)
        # Lifecycle ownership checks are independent of admission's pristine rule.
        for task, kind, owner, state in (
            (run['source_task_id'], 'seq_source_waiting', run_id, run['state']),
            (run['carrier_task_id'], 'seq_carrier', run_id, run['state']),
            *((visit['task_id'], 'seq_visit', visit['id'], visit['state']) for visit in touched),
        ):
            _require(read_sequential_owner(conn, task_id=task, org_slug=org_slug) == (kind, owner, state),
                     'ownership_relation_conflict')
        for visit in touched:
            native = _native_rows(conn, 'tasks', ('parent_task_id', 'assigned_agent', 'current_session_id',
                'active_chain', 'active_fanout'), (('text',), ('text',), ('text', 'null'), ('null',), ('null',)),
                visit['task_id'], budget)
            _require(native == [(run['carrier_task_id'], visit['principal'], visit['session_id'], None, None)],
                     'ownership_native_conflict')
        budget.check()
    return budget
