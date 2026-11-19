"""Selected pre-COMMIT validation for inert U3b/U3c writers.

The complete reader remains the offline oracle, including older-row tampering.
This incremental validator never acquires earlier result/output payloads.
"""
from __future__ import annotations

import sqlite3
from typing import Any
from collections.abc import Iterator

from runtime.infrastructure.workflow_schema import _candidate_reserve, _execute_ddl, _schema_snapshot, _structure_cells
from runtime.infrastructure.workflow_sequential_schema import sequential_ddl
from runtime.infrastructure.workflow_sequential_ownership import (
    sequential_ownership_ddl, read_sequential_owner, sequential_binding_view_ddl,
)
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows
from runtime.workflows.sequential_contracts import Counters, Limits, NextEdge, OutputRef, TerminalEdge
from runtime.workflows.sequential_definition import CompiledDefinition, parse_result, parse_start, parse_transition, validate_start
from runtime.workflows.sequential_reducer import ActiveVisit, AuthenticatedResult, RunState, reduce_result
from runtime.workflows.sequential_history import (
    StorageSettings, _storage_settings, _settings_bindings, _ReadBudget, _auxiliary, _bytes, _decode, _definitions,
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
    schema_version = 2 if any(row[0] == 'workflow_seq_source_wait_sets' for row in actual[1]) else 1
    expected = sqlite3.connect(':memory:')
    try:
        with _sql_budget(expected, budget):
            _execute_ddl(expected, sequential_ddl(schema_version))
            expected.execute(sequential_ownership_ddl(schema_version))
            expected.execute(sequential_binding_view_ddl(document, coding_extension=coding, schema_version=schema_version))
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
        snapshot = _storage_settings(_hashed(row['snapshot_bytes'], row['snapshot_digest']), budget)
        definition = definitions[row['definition_id']]
        _require(snapshot.definition == definition.pin and row['definition_identity'] == definition.pin.identity,
                 'settings_pin_conflict')
        _settings_bindings(snapshot, definition)
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


def _original_pins(conn: sqlite3.Connection, metadata: dict, *, org_slug: str, definition_id: str,
                   settings_id: str, budget: _ReadBudget) -> tuple[CompiledDefinition, dict, StorageSettings]:
    """Authenticate immutable pins; later defaults/admission do not re-pin a run."""
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
    return definitions[definition_id], setting, snapshots[settings_id]


def _pins(conn: sqlite3.Connection, metadata: dict, *, org_slug: str, definition_id: str,
          settings_id: str, budget: _ReadBudget) -> tuple[CompiledDefinition, dict, StorageSettings]:
    """Fresh admission alone requires the latest settings generation."""
    compiled, setting, snapshot = _original_pins(conn, metadata, org_slug=org_slug,
        definition_id=definition_id, settings_id=settings_id, budget=budget)
    newer = list(_bounded_rows(conn, 'workflow_seq_settings', ('generation',), (('integer',),), budget,
        where='org_slug=? AND definition_identity=? AND generation>?',
        args=(org_slug, setting['definition_identity'], setting['generation']), maximum=1))
    _require(not newer, 'settings_generation_stale')
    return compiled, setting, snapshot


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
        if run.get('engine_version', 1) == 2:
            # The complete reader accepts later S4a history; admission replay
            # must refuse it before returning a later event ID or reserving writes.
            _require(run['event_count'] == 1, 'admission_replay_incomplete')
            compiled, setting, snapshot = _original_pins(conn, metadata, org_slug=org_slug,
                definition_id=run['definition_id'], settings_id=run['settings_id'], budget=budget)
            generic = {table: _read_selected(conn, metadata, table, budget, 'run_id=?', (run_id,))
                for table in ('workflow_seq_assignments', 'workflow_seq_visits', 'workflow_seq_events',
                              'workflow_seq_outbox', 'workflow_seq_outputs', 'workflow_seq_evidence',
                              'workflow_seq_controls', 'workflow_seq_node_counters')}
            _require(not evidence_ids, 'generic_lifecycle_unavailable')
            _generic_admission_history(conn, org_slug=org_slug, run=run, compiled=compiled, settings=setting,
                snapshot=snapshot, data=generic, budget=budget)
            # Admission is a non-cancel writer too. Validate the complete org,
            # including retained families and every other unfinished @2 run,
            # before committing any allocation. Start grammar/limits are unchanged.
            from runtime.workflows.sequential_control import validate_control_write
            validate_control_write(conn, org_slug=org_slug, deadline=budget.deadline,
                                   reserve_cancellation=True)
            budget.check()
            return budget
        compiled, settings, snapshot = _original_pins(conn, metadata, org_slug=org_slug,
            definition_id=run['definition_id'], settings_id=run['settings_id'], budget=budget)
        for table, where, args, omit in (
            ('workflow_seq_schema_versions', 'version=?', (2 if 'workflow_seq_source_wait_sets' in metadata else 1,), ()),
            ('workflow_seq_visits', 'run_id=?', (run_id,), ('result_bytes', 'verified_checks_bytes')),
            ('workflow_seq_events', 'run_id=?', (run_id,), ()),
            ('workflow_seq_outbox', 'event_id=?', (run['last_event_id'],), ()),
        ):
            data[table] = _read_selected(conn, metadata, table, budget, where, args, omit=omit)
        data['workflow_seq_evidence'] = (_read_selected(conn, metadata, 'workflow_seq_evidence', budget,
            'id IN (' + ','.join('?' for _ in evidence_ids) + ')', evidence_ids) if evidence_ids else [])
        _require({row['id'] for row in data['workflow_seq_evidence']} == set(evidence_ids), 'evidence_missing')
        _types(data, budget)
        schema_version = 2 if 'workflow_seq_source_wait_sets' in metadata else 1
        _require(data['workflow_seq_schema_versions'] == [{'version': schema_version}], 'schema_marker_invalid')
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
        _auxiliary(conn, data, {run_id: run}, {visit['id']: visit}, {event['id']: event}, budget, {run['definition_id']: compiled})
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
        if run.get('engine_version', 1) == 2:
            compiled, settings, snapshot = _original_pins(conn, metadata, org_slug=org_slug,
                definition_id=run['definition_id'], settings_id=run['settings_id'], budget=budget)
            data = {table: _read_selected(conn, metadata, table, budget, 'run_id=?', (run_id,))
                for table in ('workflow_seq_assignments', 'workflow_seq_visits', 'workflow_seq_events',
                              'workflow_seq_outbox', 'workflow_seq_outputs', 'workflow_seq_evidence',
                              'workflow_seq_controls', 'workflow_seq_node_counters')}
            _require(set(evidence_ids) <= {row['id'] for row in data['workflow_seq_evidence']}, 'evidence_missing')
            _generic_admission_history(conn, org_slug=org_slug, run=run, compiled=compiled, settings=settings,
                snapshot=snapshot, data=data, budget=budget)
            budget.check()
            return budget
        _require(run.get('engine_version', 1) == 1, 'schema2_runtime_history_unavailable')
        compiled, settings, snapshot = _original_pins(conn, metadata, org_slug=org_slug,
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
        from runtime.workflows.sequential_repository_subject import read_subject_rows, validate_subject
        validate_subject(conn, rows=read_subject_rows(conn, run_id, budget), run=run, visits=by_id,
                         compiled=compiled, budget=budget)
        active = by_id.get(run['active_visit_id'])
        from runtime.workflows.sequential_business_hold import read_hold_rows, validate_business_holds
        observations = read_hold_rows(conn, run_id, budget)
        hold = validate_business_holds(conn, rows=observations, run=run, visits=by_id,
            events=events, compiled=compiled, budget=budget)
        if hold is not None and run['state'] == 'held':
            _require(active is not None and active['id'] == hold['visit_id']
                     and events[-1]['id'] == hold['last_event_id'], 'business_hold_prefix_conflict')
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
                             and source['selected'] == 0 and run['state'] == ('held' if hold is not None else 'active'),
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
                   {run_id: run}, by_id, {row['id']: row for row in events}, budget, {run['definition_id']: compiled})
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


def _generic_admission_history(conn: sqlite3.Connection, *, org_slug: str, run: dict,
                               compiled: object, settings: dict, snapshot: object,
                               data: dict[str, list[dict]], budget: _ReadBudget) -> None:
    """Inert S3 admission and S4a lifecycle; no S5 wait-set authority."""
    from runtime.infrastructure.workflow_task_ownership import selected_owner
    from runtime.workflows.sequential_assignments import validate_assignment_snapshot
    from runtime.workflows.sequential_contracts import GenericLimits
    from runtime.workflows.sequential_definition import generic_limits, parse_generic_start, validate_generic_start
    from runtime.workflows.sequential_engine import _identity
    from runtime.workflows.sequential_history import GenericStorageSettings

    # Schema-only @2 reference rows pinned to @1 settings remain unsupported;
    # permitting S4a counters must not change their explicit refusal contract.
    _require(isinstance(snapshot, GenericStorageSettings), 'schema2_runtime_history_unavailable')
    _require(run['engine_version'] == 2, 'generic_admission_pin_conflict')
    start = _parse(_hashed(run['start_bytes'], run['start_digest']), parse_generic_start, budget)
    original = GenericLimits.model_validate(_decode(run['original_limits_bytes'], budget))
    validate_generic_start(start, compiled, original_limits=original)
    _require(original == snapshot.limits and start.operation_key == run['start_operation_key']
             and start.settings.generation == settings['generation']
             and start.settings.snapshot_digest == settings['snapshot_digest'] and snapshot.admission == 'Allowed'
             and generic_limits(compiled, start.lower_limits) == generic_limits(compiled, original, start.lower_limits)
             and all(getattr(start.lower_limits, key) <= getattr(original, key)
                     for key in ('max_visits', 'max_events', 'max_recoveries')),
             'generic_admission_pin_conflict')
    raw = _hashed(run['assignment_snapshot_bytes'], run['assignment_snapshot_digest'])
    resolved = _decode(raw, budget)
    _require(_bytes(resolved) == raw, 'assignment_snapshot_canonical')
    validate_assignment_snapshot(resolved, compiled, start, defaults=snapshot.bindings, org_slug=org_slug)
    if run['event_count'] > 1 or any(row['run_id'] == run['id'] and row['session_id'] is not None
            for row in data['workflow_seq_visits']) or any(row['run_id'] == run['id'] for row in data['workflow_seq_evidence']):
        _generic_control_history(conn, org_slug=org_slug, run=run, compiled=compiled,
                                 data=data, resolved=resolved, budget=budget)
        return
    selected = {}
    for table in ('workflow_seq_visits', 'workflow_seq_events', 'workflow_seq_assignments', 'workflow_seq_outbox'):
        budget.work(len(data[table]))
        selected[table] = [row for row in data[table] if row['run_id'] == run['id']]
    visits, events = selected['workflow_seq_visits'], selected['workflow_seq_events']
    _require(len(visits) == len(events) == len(selected['workflow_seq_outbox']) == 1
             and len(selected['workflow_seq_assignments']) == len(resolved['nodes']), 'generic_admission_closure_invalid')
    visit, event = visits[0], events[0]
    entry = next(node for node in resolved['nodes'] if node['node'] == compiled.definition.entry_node)
    assignments = {row['node_id']: row for row in selected['workflow_seq_assignments']}
    _require(len(assignments) == len(resolved['nodes']), 'assignment_nodes_conflict')
    for node in resolved['nodes']:
        stored = assignments.get(node['node'])
        _require(stored is not None and (stored['id'], stored['generation'], stored['principal'], stored['predecessor_id']) ==
                 (_identity('assignment', run['id'], node['node'], '1'), 1, node['principal'], None)
                 and _hashed(stored['snapshot_bytes'], stored['snapshot_digest']) == _bytes(node), 'assignment_row_conflict')
    _require((visit['engine_version'], visit['node_id'], visit['ordinal'], visit['epoch'], visit['predecessor_event_id'],
              visit['principal'], visit['session_id'], visit['result_id'], visit['result_bytes'], visit['result_digest'],
              visit['verified_checks_bytes'], visit['generic_checks_bytes'], visit['state'], visit['selected'],
              visit['assignment_id'], visit['assignment_generation']) ==
             (2, entry['node'], 1, None, None, entry['principal'], None, None, None, None, None, None, 'queued', 1,
              assignments[entry['node']]['id'], 1)
             and _hashed(visit['input_selection_bytes'], visit['input_selection_digest']) == _bytes(dict(outputs=[])),
             'generic_entry_visit_invalid')
    payload = _decode(_hashed(event['event_bytes'], event['event_digest']), budget)
    _require(payload == dict(format='workflow-sequential-storage-event@2', kind='admitted', run_id=run['id'],
        sequence=1, operation_key=start.operation_key, prior_digest='0' * 64,
        assignment_snapshot_digest=run['assignment_snapshot_digest'])
        and event['event_bytes'] == _bytes(payload)
        and (event['engine_version'], event['kind'], event['sequence'], event['prior_digest'], event['operation_key'],
             event['source_visit_id'], event['source_result_id']) == (2, 'admitted', 1, '0' * 64, start.operation_key, None, None),
        'generic_admitted_event_invalid')
    _require((run['state'], run['progress'], run['cas'], run['event_count'], run['visit_count'], run['epoch'],
              run['content_returns'], run['recovery_count'], run['assignment_generation'], run['held_node_id'],
              run['held_event_id'], run['terminal_outcome'], run['last_event_id'], run['active_visit_id']) ==
             ('running', 'queued', 0, 1, 1, None, None, 0, 1, None, None, None, event['id'], visit['id']),
             'generic_admission_state_unavailable')
    for table in ('workflow_seq_outputs', 'workflow_seq_evidence'):
        budget.work(len(data[table]))
        _require(not any(row['run_id'] == run['id'] for row in data[table]), 'generic_lifecycle_unavailable')
    _result(conn, run['source_result_id'], run['source_task_id'], run['source_principal'], run['source_session_id'], budget)
    # Source is deliberately still ordinary. S5 supplies plural wait membership;
    # S3 must not call the @1 source adopter against schema2's @2 binding view.
    _require(selected_owner(conn, run['source_task_id'], org_slug) == ('legacy', None, None), 'generic_source_conflict')
    source_rows = _native_rows(conn, 'tasks', ('assigned_agent', 'team'), (('text',), ('text',)), run['source_task_id'], budget)
    _require(source_rows == [(run['source_principal'], resolved['team'])], 'generic_source_conflict')
    for task, kind, owner, parent, principal in (
        (run['carrier_task_id'], 'seq_carrier', run['id'], run['source_task_id'], run['source_principal']),
        (visit['task_id'], 'seq_visit', visit['id'], run['carrier_task_id'], entry['principal']),
    ):
        _require(selected_owner(conn, task, org_slug) == (kind, owner, 'running' if kind == 'seq_carrier' else 'queued'),
                 'ownership_relation_conflict')
        native = _native_rows(conn, 'tasks', ('parent_task_id', 'assigned_agent', 'team', 'task_type', 'status',
            'current_session_id', 'active_chain', 'active_fanout', 'model_override', 'reasoning_effort_override', 'override_source'),
            (('text',), ('text',), ('text',), ('text',), ('text',), ('null',), ('null',), ('null',),
             ('text', 'null'), ('text', 'null'), ('text', 'null')), task, budget)
        expected = entry['selection'] if kind == 'seq_visit' else dict(model_override=None, reasoning_effort_override=None, override_source=None)
        _require(native == [(parent, principal, resolved['team'], 'subtask', 'pending', None, None, None,
            expected['model_override'], expected['reasoning_effort_override'], expected['override_source'])], 'generic_native_selection_conflict')
    _auxiliary(conn, {**selected, 'workflow_seq_evidence': []}, {run['id']: run}, {visit['id']: visit},
               {event['id']: event}, budget, {run['definition_id']: compiled})


def _generic_control_history(conn: sqlite3.Connection, *, org_slug: str, run: dict, compiled: object,
                             data: dict[str, list[dict]], resolved: dict, budget: _ReadBudget) -> None:
    """Authenticate immutable holds/controls and reconstruct their mutable projection."""
    import hashlib
    from runtime.infrastructure.workflow_task_ownership import selected_owner
    from runtime.workflows.sequential_business_hold import _proof, generic_callback_bytes, generic_closed_visit, generic_hold_cause
    from runtime.workflows.sequential_control import _validate_control_assignment, parse_control, result_counters
    from runtime.workflows.sequential_contracts import GenericLimits
    from runtime.workflows.sequential_definition import generic_limits, parse_generic_start
    from runtime.workflows.sequential_engine import _identity
    from runtime.workflows.sequential_reducer import reduce_generic_control, require_generic_event_capacity
    selected = {table: [row for row in rows if row.get('run_id') == run['id']] for table, rows in data.items()}
    visits = sorted(selected['workflow_seq_visits'], key=lambda row: row['ordinal'])
    events = sorted(selected['workflow_seq_events'], key=lambda row: row['sequence'])
    controls = selected['workflow_seq_controls']
    assignments = {row['id']: row for row in selected['workflow_seq_assignments']}
    for initial in resolved['nodes']:
        key = _identity('assignment', run['id'], initial['node'], '1')
        row = assignments.get(key)
        _require(row is not None and (row['node_id'], row['generation'], row['principal'], row['predecessor_id']) ==
            (initial['node'], 1, initial['principal'], None)
            and _hashed(row['snapshot_bytes'], row['snapshot_digest']) == _bytes(initial), 'assignment_row_conflict')
    _require(0 < len(visits) <= 64 and 0 < len(events) <= 4096
        and [row['ordinal'] for row in visits] == list(range(1, len(visits) + 1))
        and [row['sequence'] for row in events] == list(range(1, len(events) + 1)), 'generic_history_order')
    first = visits[0]
    start = parse_generic_start(run['start_bytes'])
    entry = next(row for row in resolved['nodes'] if row['node'] == compiled.definition.entry_node)
    _require((first['node_id'], first['principal'], first['predecessor_event_id']) == (entry['node'], entry['principal'], None)
        and first['input_selection_bytes'] == _bytes(dict(outputs=[])), 'generic_entry_visit_invalid')
    current = dict(run, state='running', progress='queued', active_visit_id=first['id'], held_node_id=None,
        held_event_id=None, terminal_outcome=None, cas=0, event_count=1, visit_count=1, recovery_count=0,
        assignment_generation=1, last_event_id=events[0]['id'])
    limits = generic_limits(compiled, GenericLimits.model_validate(_decode(run['original_limits_bytes'], budget)), start.lower_limits)
    seen_visits, seen_events, seen_controls = [first], [], []
    prior = '0' * 64
    selections, findings, revision_rows = (), (), []
    closed_visits = set()  # Proofs already authenticated in THIS reader snapshot.
    current['progress'] = 'executing' if first['session_id'] else 'queued'
    for event in events:
        budget.step()
        cancel_append = event['kind'] == 'cancellation_finished'
        body = _decode(_hashed(event['event_bytes'], event['event_digest']), budget)
        _require(event['engine_version'] == 2 and event['prior_digest'] == prior
            and body['format'] == 'workflow-sequential-storage-event@2' and body['run_id'] == run['id']
            and body['kind'] == event['kind'] and body['sequence'] == event['sequence']
            and body['operation_key'] == event['operation_key'] and body['prior_digest'] == prior
            and _bytes(body) == event['event_bytes'], 'generic_event_conflict')
        prior = event['event_digest']
        if event['sequence'] == 1:
            _require(body == dict(format='workflow-sequential-storage-event@2', kind='admitted', run_id=run['id'],
                sequence=1, operation_key=start.operation_key, prior_digest='0' * 64,
                assignment_snapshot_digest=run['assignment_snapshot_digest']), 'generic_admitted_event_invalid')
        elif event['kind'] == 'transition':
            from runtime.workflows.sequential_definition import parse_generic_callback, parse_generic_result
            from runtime.workflows.sequential_reducer import (
                GenericActiveVisit, GenericAuthenticatedResult, GenericRunState, reduce_generic_result,
            )
            visit = next((row for row in visits if row['id'] == event['source_visit_id']), None)
            _require(visit is not None and current['state'] == 'running'
                and current['active_visit_id'] == visit['id'] and current['progress'] != 'cancelling', 'generic_result_conflict')
            envelope = _parse(_hashed(visit['result_bytes'], visit['result_digest']), parse_generic_result, budget)
            _result(conn, event['source_result_id'], visit['task_id'], visit['principal'], visit['session_id'], budget)
            proof = generic_closed_visit(conn, run=run, visit=visit, budget=budget)
            closed_visits.add(visit['id'])
            _require(proof['result_id'] == visit['result_id'] == event['source_result_id'], 'generic_result_conflict')
            payload = generic_callback_bytes(conn, visit['result_id'], budget)
            _require(payload is not None, 'generic_result_conflict')
            node = next(node for node in compiled.definition.nodes if node.id == visit['node_id'])
            callback = parse_generic_callback(payload[0], contract=node.output_contract.type)
            import base64
            content = base64.b64decode(callback.content_base64, validate=True)
            terminal, _ = _proof(
                conn, run['id'], visit['id'], 'terminal', budget)
            native_status = conn.execute('SELECT status FROM task_results WHERE id=?', (visit['result_id'],)).fetchone()[0]
            _require(native_status == 'completed' and terminal['classification'] == 'success'
                and terminal['receipt']['terminal_reason'] == 'success', 'generic_result_unsettled')
            _require(callback.outcome == envelope.outcome
                and list(callback.findings) == [finding.text for finding in envelope.findings]
                and visit['generic_checks_bytes'] == _bytes(dict(settled=True)), 'generic_result_conflict')
            counts = result_counters(current, compiled, seen_visits, seen_events, seen_controls, revision_rows)
            state = GenericRunState(run_id=run['id'], definition_digest=compiled.pin.definition_digest,
                original_limits=limits, lower_limits=limits, counters=counts,
                active_visit=GenericActiveVisit(node=visit['node_id'], visit_id=visit['id'], task_id=visit['task_id'],
                    session_id=visit['session_id'], ordinal=visit['ordinal'], input_selection_digest=visit['input_selection_digest']),
                selections=selections, findings=findings, receipts=())
            decision = reduce_generic_result(compiled, state, GenericAuthenticatedResult(task_id=visit['task_id'],
                session_id=visit['session_id'], result_id=visit['result_id'], envelope=envelope, content=content, settled=True),
                storage_event_count=current['event_count'])
            _require(body['decision'] == decision.model_dump(mode='json') and visit['selected'] == 0
                and visit['state'] == ('held' if decision.edge.action == 'hold' else 'completed'), 'generic_result_conflict')
            rows = [row for row in selected['workflow_seq_outputs'] if row['visit_id'] == visit['id']]
            _require(len(rows) == len(envelope.outputs), 'generic_output_conflict')
            from runtime.workflows.sequential_document_access import document_recipients
            for row, ref in zip(rows, envelope.outputs, strict=True):
                _require(row['id'] == _identity('output', visit['id'], ref.slot) and row['engine_version'] == 2
                    and row['content_bytes'] == content and row['epoch'] is None
                    and (row['slot'], row['contract'], row['task_id'], row['session_id'], row['result_id'], row['content_digest']) ==
                        (ref.slot, ref.contract, ref.task_id, ref.session_id, ref.result_id, ref.digest)
                    and row['recipients_bytes'] == _bytes(dict(roles=list(document_recipients(compiled, ref.slot, budget)))),
                    'generic_output_conflict')
            selections, findings = decision.selections, decision.findings
            revision_rows = [dict(node_id=key, revisions=count.revisions) for key, count in decision.after.nodes]
            if decision.activation is not None:
                activation = decision.activation
                successor = next((row for row in visits if row['ordinal'] == activation.ordinal), None)
                _require(successor is not None and successor['node_id'] == activation.node
                    and successor['predecessor_event_id'] == event['id']
                    and generic_selection(successor, budget) == activation.inputs, 'generic_successor_conflict')
                current.update(state='running', progress='executing' if successor['session_id'] else 'queued',
                    active_visit_id=successor['id'], visit_count=decision.after.visits)
                seen_visits.append(successor)
            elif decision.edge.action == 'hold':
                current.update(state='held', progress=None, active_visit_id=None, held_node_id=visit['node_id'],
                    held_event_id=event['id'], hold_cause=decision.reason)
            else:
                current.update(state='finished', progress=None, active_visit_id=None, terminal_outcome='failed' if decision.edge.action == 'fail' else 'completed')
        elif event['kind'] == 'held':
            visit = next((row for row in visits if row['id'] == body.get('visit_id')), None)
            _require(visit is not None and current['state'] == 'running' and current['progress'] != 'cancelling'
                and current['active_visit_id'] == visit['id'] and body['node'] == visit['node_id']
                and visit['selected'] == 0 and visit['state'] in ('held', 'cancelled'), 'generic_hold_state_conflict')
            cause, proof = generic_hold_cause(conn, run=run, visit=visit, compiled=compiled, budget=budget,
                                             unavailable=body['cause'] == 'assignee_unavailable')
            closed_visits.add(visit['id'])
            _require((body['cause'], body['closure'], event['source_visit_id'], event['source_result_id']) ==
                (cause, proof, visit['id'], proof['result_id']), 'generic_hold_proof_conflict')
            current.update(state='held', progress=None, active_visit_id=None, held_node_id=visit['node_id'], held_event_id=event['id'], hold_cause=cause)
        elif event['kind'] in ('control', 'cancellation_finished'):
            rows = [row for row in controls if row['event_id'] == event['id']]
            if rows:
                _require(len(rows) == 1, 'control_receipt_conflict')
                row = rows[0]
                command = parse_control(_hashed(row['request_bytes'], row['request_digest']))
                cancel_append = command.action == 'cancel'
                _require((row['run_id'], row['operation_key'], row['expected_cas'], row['expected_held_event_id'], row['action']) ==
                    (command.run_id, command.operation_key, command.expected_cas, command.expected_held_event_id, command.action),
                    'control_identity_conflict')
                _result(conn, row['actor_result_id'], row['actor_task_id'], row['actor_principal'], row['actor_session_id'], budget)
                _require(conn.execute('SELECT status FROM task_results WHERE id=?', (row['actor_result_id'],)).fetchone()[0] == 'completed',
                         'control_actor_conflict')
                _require((row['actor_task_id'], row['actor_principal']) == (run['source_task_id'], run['source_principal'])
                    and body['actor'] == dict(task_id=row['actor_task_id'], principal=row['actor_principal'],
                        session_id=row['actor_session_id'], result_id=row['actor_result_id'])
                    and body['command_digest'] == row['request_digest'], 'control_actor_conflict')
                counts = result_counters(current, compiled, seen_visits, seen_events, seen_controls, revision_rows)
                _require(body['before_counters'] == counts.model_dump(mode='json'), 'control_counter_conflict')
                disposition = reduce_generic_control(compiled, command, run=current, counters=counts, limits=limits)
                new_visit = next((visit for visit in visits if visit['id'] == row['new_visit_id']), None)
                expected_receipt = dict(format='workflow-sequential-control-receipt@2', run_id=run['id'], event_id=event['id'],
                    new_visit_id=row['new_visit_id'], new_task_id=None if new_visit is None else new_visit['task_id'])
                _require(_hashed(row['receipt_bytes'], row['receipt_digest']) == _bytes(expected_receipt), 'control_receipt_conflict')
                if disposition == 'replace':
                    previous = next(visit for visit in seen_visits if visit['id'] == body['replaced_visit_id'])
                    generic_closed_visit(conn, run=run, visit=previous, budget=budget)
                    closed_visits.add(previous['id'])
                    _require(new_visit is not None and new_visit['node_id'] == current['held_node_id']
                        and new_visit['predecessor_event_id'] == event['id'] and body['new_visit_id'] == new_visit['id']
                        and (new_visit['input_selection_bytes'], new_visit['input_selection_digest']) ==
                            (previous['input_selection_bytes'], previous['input_selection_digest']), 'control_replacement_conflict')
                    assignment = assignments.get(new_visit['assignment_id'])
                    _require(assignment is not None and _hashed(assignment['snapshot_bytes'], assignment['snapshot_digest']) ==
                        _bytes(body['assignment']['value']) and new_visit['principal'] == assignment['principal']
                        and assignment['predecessor_id'] == previous['assignment_id']
                        and assignment['generation'] == previous['assignment_generation'] + 1, 'control_assignment_conflict')
                    predecessor = assignments[previous['assignment_id']]
                    _validate_control_assignment(command, compiled=compiled,
                        previous=_decode(_hashed(predecessor['snapshot_bytes'], predecessor['snapshot_digest']), budget),
                        value=body['assignment']['value'], authority=body['assignment']['authority'], run=run,
                        history=list(assignments.values()))
                    old_native = conn.execute('SELECT brief FROM tasks WHERE id=?', (previous['task_id'],)).fetchone()
                    new_native = conn.execute('SELECT brief FROM tasks WHERE id=?', (new_visit['task_id'],)).fetchone()
                    _require(old_native is not None and new_native is not None
                        and hashlib.sha256(old_native[0].encode('utf8')).hexdigest() == body['original_brief_digest']
                        and hashlib.sha256(new_native[0].encode('utf8')).hexdigest() == body['new_brief_digest'], 'control_brief_conflict')
                    current.update(state='running', progress='executing' if new_visit['session_id'] else 'queued', active_visit_id=new_visit['id'], held_node_id=None,
                        held_event_id=None, visit_count=current['visit_count'] + 1, recovery_count=current['recovery_count'] + 1,
                        assignment_generation=current['assignment_generation'] + 1)
                    seen_visits.append(new_visit)
                elif event['kind'] == 'cancellation_finished':
                    if current['active_visit_id'] is not None:
                        visit = next(visit for visit in visits if visit['id'] == current['active_visit_id'])
                        generic_closed_visit(conn, run=run, visit=visit, budget=budget, cancellation=True)
                        closed_visits.add(visit['id'])
                    current.update(state='finished', terminal_outcome='cancelled', active_visit_id=None,
                                   held_node_id=None, held_event_id=None, progress=None)
                else:
                    _require(current['state'] == 'running', 'control_cancel_conflict')
                    current['progress'] = 'cancelling'
                seen_controls.append(row)
            else:
                _require(event['kind'] == 'cancellation_finished' and current['progress'] == 'cancelling', 'control_missing')
                visit = next(visit for visit in visits if visit['id'] == current['active_visit_id'])
                _require(body['closure'] == generic_closed_visit(conn, run=run, visit=visit, budget=budget,
                    cancellation=True), 'control_cancel_proof')
                closed_visits.add(visit['id'])
                current.update(state='finished', terminal_outcome='cancelled', active_visit_id=None,
                               held_node_id=None, held_event_id=None, progress=None)
        else:
            _require(False, 'generic_lifecycle_unavailable')
        if event['sequence'] > 1:
            require_generic_event_capacity(current['event_count'], resulting_state=current['state'], cancel=cancel_append)
            current.update(cas=current['cas'] + 1, event_count=current['event_count'] + 1, last_event_id=event['id'])
        seen_events.append(event)
    if current['state'] == 'running' and current['progress'] != 'cancelling':
        active = next(visit for visit in visits if visit['id'] == current['active_visit_id'])
        terminal = any(row['visit_id'] == active['id'] and row['kind'] == 'terminal'
            for row in selected['workflow_seq_evidence'])
        current['progress'] = 'result_pending_settlement' if terminal else 'executing' if active['session_id'] else 'queued'
    _require(all(run[key] == current[key] for key in ('state', 'progress', 'active_visit_id', 'held_node_id', 'held_event_id',
        'terminal_outcome', 'cas', 'event_count', 'visit_count', 'recovery_count', 'assignment_generation', 'last_event_id'))
        and len(seen_visits) == len(visits) and len(seen_controls) == len(controls), 'generic_projection_conflict')
    _require([visit['id'] for visit in visits if visit['selected']] ==
        ([] if run['active_visit_id'] is None else [run['active_visit_id']]), 'generic_outstanding_conflict')
    _require(len(assignments) == len(resolved['nodes']) + sum(row['new_visit_id'] is not None for row in controls),
             'assignment_row_conflict')
    for row in selected['workflow_seq_node_counters']:
        _require(row['node_id'] in {node.id for node in compiled.definition.nodes}
            and row['visits'] == sum(visit['node_id'] == row['node_id'] for visit in visits)
            and row['revisions'] == next((item['revisions'] for item in revision_rows if item['node_id'] == row['node_id']), 0), 'control_counter_conflict')
    for visit in visits:
        _require(visit['engine_version'] == 2 and visit['epoch'] is None
            and (visit['result_id'] is None or any(event['kind'] == 'transition' and event['source_visit_id'] == visit['id']
                                                 and event['source_result_id'] == visit['result_id'] for event in events))
            and len(generic_selection(visit, budget)) <= 32,
            'generic_visit_conflict')
        _require(selected_owner(conn, visit['task_id'], org_slug) == ('seq_visit', visit['id'], visit['state']), 'ownership_relation_conflict')
        assignment = assignments.get(visit['assignment_id'])
        _require(assignment is not None and assignment['node_id'] == visit['node_id']
            and assignment['generation'] == visit['assignment_generation'] and assignment['principal'] == visit['principal'],
            'control_assignment_conflict')
        value = _decode(_hashed(assignment['snapshot_bytes'], assignment['snapshot_digest']), budget)
        native = _native_rows(conn, 'tasks', ('parent_task_id', 'assigned_agent', 'team', 'task_type',
            'current_session_id', 'model_override', 'reasoning_effort_override', 'override_source'),
            (('text',), ('text',), ('text',), ('text',), ('text', 'null'), ('text', 'null'), ('text', 'null'), ('text', 'null')),
            visit['task_id'], budget)
        _require(native == [(run['carrier_task_id'], visit['principal'], resolved['team'], 'subtask', visit['session_id'],
            value['selection']['model_override'], value['selection']['reasoning_effort_override'], value['selection']['override_source'])],
            'generic_native_selection_conflict')
    _require(all(any(visit['id'] == row['visit_id'] and visit['result_id'] is not None for visit in visits)
        for row in selected['workflow_seq_outputs']), 'generic_output_conflict')
    _generic_native_history(conn, run, visits, selected['workflow_seq_evidence'], budget, closed_visits, resolved)
    _auxiliary(conn, selected, {run['id']: run}, {visit['id']: visit for visit in visits},
               {event['id']: event for event in events}, budget, {run['definition_id']: compiled})


def generic_selection(visit: dict, budget: _ReadBudget) -> tuple:
    from runtime.workflows.sequential_contracts import GenericOutputRef
    value = _record(visit['input_selection_bytes'], visit['input_selection_digest'], {'outputs'}, budget)
    _require(type(value['outputs']) is list and len(value['outputs']) <= 32, 'input_selection_invalid')
    result = tuple(GenericOutputRef.model_validate(item) for item in value['outputs'])
    _require(len({item.slot for item in result}) == len(result), 'input_selection_duplicate')
    return result


def generic_result_state(conn: sqlite3.Connection, run: dict, visit: dict, compiled: Any, budget: _ReadBudget) -> Any:
    """Reconstruct the original result counters and current generic selections."""
    from runtime.workflows.sequential_contracts import GenericLimits
    from runtime.workflows.sequential_control import result_counters
    from runtime.workflows.sequential_definition import parse_generic_start
    from runtime.workflows.sequential_engine import _engine_rows
    from runtime.workflows.sequential_reducer import GenericActiveVisit, GenericDecision, GenericRunState
    tables = {table: _engine_rows(conn, table, budget, 'run_id=?', (run['id'],)) for table in (
        'workflow_seq_visits', 'workflow_seq_events', 'workflow_seq_controls', 'workflow_seq_node_counters')}
    counts = result_counters(run, compiled, tables['workflow_seq_visits'], tables['workflow_seq_events'],
        tables['workflow_seq_controls'], tables['workflow_seq_node_counters'])
    previous = sorted((row for row in tables['workflow_seq_events'] if row['kind'] == 'transition'), key=lambda row: row['sequence'])
    decision = None
    for row in previous:
        body = _decode(_hashed(row['event_bytes'], row['event_digest']), budget)
        value = body['decision']
        for key in ('before', 'after'):
            value[key]['nodes'] = dict(value[key]['nodes'])
        decision = GenericDecision.model_validate(value)
    return GenericRunState(run_id=run['id'], definition_digest=compiled.pin.definition_digest,
        original_limits=GenericLimits.model_validate(_decode(run['original_limits_bytes'], budget)),
        lower_limits=parse_generic_start(run['start_bytes']).lower_limits, counters=counts,
        active_visit=GenericActiveVisit(node=visit['node_id'], visit_id=visit['id'], task_id=visit['task_id'],
            session_id=visit['session_id'], ordinal=visit['ordinal'], input_selection_digest=visit['input_selection_digest']),
        selections=() if decision is None else decision.selections,
        findings=() if decision is None else decision.findings, receipts=())


def _generic_native_history(conn: sqlite3.Connection, run: dict, visits: list[dict],
                            evidence: list[dict], budget: _ReadBudget, closed_visits: set[str], resolved: dict) -> None:
    """Bound native sessions and original proof prefixes, including before outer."""
    from runtime.infrastructure.workflow_task_ownership import selected_owner
    from runtime.workflows.sequential_business_hold import _proof, generic_closed_visit
    _result(conn, run['source_result_id'], run['source_task_id'], run['source_principal'], run['source_session_id'], budget)
    _require(selected_owner(conn, run['source_task_id'], run['org_slug']) == ('legacy', None, None), 'generic_source_conflict')
    _require(selected_owner(conn, run['carrier_task_id'], run['org_slug']) == ('seq_carrier', run['id'], run['state']),
             'ownership_relation_conflict')
    source = _native_rows(conn, 'tasks', ('assigned_agent', 'team'), (('text',), ('text',)), run['source_task_id'], budget)
    # The caller authenticated this assignment snapshot in the same read.
    _require(source == [(run['source_principal'], resolved['team'])], 'generic_source_conflict')
    carrier = _native_rows(conn, 'tasks', ('parent_task_id', 'assigned_agent', 'team', 'task_type', 'status',
        'current_session_id'), (('text',), ('text',), ('text',), ('text',), ('text',), ('null',)), run['carrier_task_id'], budget)
    _require(carrier == [(run['source_task_id'], run['source_principal'], resolved['team'], 'subtask', 'pending', None)],
             'generic_native_selection_conflict')
    for row in evidence:
        _require(row['kind'] in ('terminal', 'outer') and row['visit_id'] in {visit['id'] for visit in visits},
                 'generic_evidence_unavailable')
    for visit in visits:
        budget.step()
        native = _native_rows(conn, 'tasks', ('assigned_agent', 'current_session_id', 'status', 'completed_at'),
            (('text',), ('text', 'null'), ('text',), ('text', 'null')), visit['task_id'], budget)
        _require(len(native) == 1 and native[0][:2] == (visit['principal'], visit['session_id']), 'generic_native_session_conflict')
        proofs = [row for row in evidence if row['visit_id'] == visit['id']]
        _require(len({row['kind'] for row in proofs}) == len(proofs), 'generic_evidence_conflict')
        if visit['session_id'] is None:
            generic_closed_visit(conn, run=run, visit=visit, budget=budget)
            _require(not proofs and visit['state'] in ('queued', 'held', 'cancelled'), 'generic_native_session_conflict')
            continue
        _require(visit['state'] != 'queued' and native[0][2] in ('in_progress', 'completed', 'failed', 'cancelled'),
                 'generic_native_session_conflict')
        terminal = next((row for row in proofs if row['kind'] == 'terminal'), None)
        outer = next((row for row in proofs if row['kind'] == 'outer'), None)
        _require(outer is None or terminal is not None, 'generic_evidence_conflict')
        if terminal is None:
            _require(visit['state'] in ('running', 'cancelled') and native[0][3] is None,
                     'generic_native_session_conflict')
        else:
            from runtime.platform.session_backend import TerminalReason
            from runtime.workflows.sequential_business_hold import generic_callback_bytes
            value, _ = _proof(conn, run['id'], visit['id'], 'terminal', budget)
            common = dict(task_id=visit['task_id'], principal=visit['principal'], session_id=visit['session_id'])
            _require(value.keys() == {'format', 'invocation_id', 'task_id', 'principal', 'session_id',
                'classification', 'request', 'handle_digest', 'receipt', 'callback'}
                and value['format'] == 'workflow-sequential-terminal@1'
                and type(value['invocation_id']) is str and bool(value['invocation_id'])
                and all(value[key] == item for key, item in common.items())
                and type(value['handle_digest']) is str and len(value['handle_digest']) == 64
                and all(char in '0123456789abcdef' for char in value['handle_digest'])
                and type(value['request']) is dict
                and value['request'].keys() == {'org', 'logical_id', 'agent_id', 'retry_attempt'}
                and value['request']['org'] == run['org_slug'] and value['request']['agent_id'] == visit['principal']
                and type(value['request']['logical_id']) is str and bool(value['request']['logical_id'])
                and type(value['request']['retry_attempt']) is int and value['request']['retry_attempt'] == 0,
                'generic_terminal_conflict')
            receipt = value['receipt']
            _require(type(receipt) is dict and receipt.keys() == {'backend', 'terminal_reason', 'cleanup_status', 'quiescent', 'survivors'}
                and receipt['backend'] in ('linux-systemd-cgroup-v2', 'macos-process-group')
                and receipt['terminal_reason'] in tuple(reason.value for reason in TerminalReason)
                and receipt['quiescent'] is True and receipt['survivors'] == []
                and receipt['cleanup_status'] in ('clean', 'term', 'kill')
                and native[0][2] in ('completed', 'failed', 'cancelled') and bool(native[0][3]),
                'generic_terminal_conflict')
            callback = value['callback']
            if callback is None:
                budget.step()
                _require(conn.execute('SELECT 1 FROM task_results WHERE task_id=? AND session_id=? LIMIT 1',
                    (visit['task_id'], visit['session_id'])).fetchone() is None
                    and value['classification'] == 'unknown', 'generic_callback_conflict')
            else:
                _require(type(callback) is dict and callback.keys() == {'task_id', 'principal', 'session_id', 'result_id', 'status', 'carrier_digest'}
                    and all(callback[key] == item for key, item in common.items()), 'generic_callback_conflict')
                _result(conn, callback['result_id'], visit['task_id'], visit['principal'], visit['session_id'], budget)
                status = conn.execute('SELECT status FROM task_results WHERE id=?', (callback['result_id'],)).fetchone()[0]
                classification = ('success' if receipt['terminal_reason'] == 'success' and status == 'completed'
                    else 'failed' if status == 'failed' else 'blocked' if status == 'blocked' else 'unknown')
                _require(status == callback['status'] and value['classification'] == classification, 'generic_callback_conflict')
                # The event reconstruction already authenticates closed payloads.
                # Charge an unclosed prefix once; outer validation owns the rest.
                if visit['id'] not in closed_visits and outer is None:
                    if run['progress'] == 'cancelling' or visit['state'] == 'cancelled':
                        from runtime.workflows.sequential_business_hold import _generic_callback_digest
                        digest = _generic_callback_digest(conn, callback['result_id'], budget)
                    else:
                        payload = generic_callback_bytes(conn, callback['result_id'], budget)
                        digest = None if payload is None else payload[1]
                    _require(digest is None or digest == callback['carrier_digest'], 'generic_callback_conflict')
        if outer is not None and visit['id'] not in closed_visits:
            generic_closed_visit(conn, run=run, visit=visit, budget=budget,
                cancellation=run['progress'] == 'cancelling' or visit['state'] == 'cancelled')
