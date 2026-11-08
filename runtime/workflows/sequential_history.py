"""Standalone U2a complete sequential-history and combined-structure reader.

No normal startup, allocator, route or producer imports this module. The result
is storage integrity, not current authorization, native settlement or engine
availability. U2b must compose retained family data readers at installation and
reopen before any runtime attachment; U3 must authenticate adapter assertions.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BeforeValidator, Field

from runtime.infrastructure.workflow_schema import (
    _canonical_connection, _execute_ddl, _layout, _read_workflow_schema,
    _schema_snapshot,
)
from runtime.infrastructure.workflow_sequential_ownership import (
    SEQUENTIAL_OWNERSHIP_DDL, sequential_binding_view_ddl,
    validate_sequential_ownership,
)
from runtime.infrastructure.workflow_sequential_schema import (
    CANONICAL_WORKFLOW_SEQUENTIAL_DDL, SEQUENTIAL_TABLES,
)
from runtime.infrastructure.workflow_task_ownership import (
    OWNERSHIP_DDL, binding_view_ddl, validate_ownership_data,
)
from runtime.workflows.sequential_contracts import (
    BindingClaim, Closed, Counters, DefinitionPin, Limits, NextEdge, OutputRef,
    TerminalEdge, _array,
)
from runtime.workflows.sequential_definition import (
    CompiledDefinition, _json, compile_definition, parse_result, parse_start,
    parse_transition, validate_start,
)
from runtime.workflows.sequential_reducer import (
    ActiveVisit, AuthenticatedResult, Decision, RunState, reduce_result,
)


class StorageSettings(Closed):
    """Internal snapshot encoding, not the future public settings command."""

    format: Literal['workflow-sequential-settings-storage@1']
    definition: DefinitionPin
    bindings: Annotated[tuple[BindingClaim, ...], BeforeValidator(_array), Field(min_length=1, max_length=16)]
    limits: Limits
    admission: Literal['Disabled', 'Allowed']


@dataclass(frozen=True)
class SequentialLayout:
    document: str | None
    coding: bool
    owner_indexed: bool
    ownership: int
    sequential: bool


@dataclass(frozen=True)
class SequentialHistory:
    layout: SequentialLayout
    status: Literal['new-engine unavailable', 'storage readable']
    definition_ids: tuple[str, ...]
    run_ids: tuple[str, ...]


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('workflow_seq_' + code)


def _bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf8')


def _hashed(raw: object, digest: object) -> bytes:
    _require(type(raw) is bytes and 0 < len(raw) <= 1048576, 'bytes_invalid')
    _require(type(digest) is str and hashlib.sha256(raw).hexdigest() == digest, 'digest_invalid')
    return raw


def _record(raw: object, digest: object, keys: set[str]) -> dict:
    value = _json(_hashed(raw, digest), 1048576)
    _require(value.keys() == keys, 'record_fields_invalid')
    return value


def _rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    # All table names are literals selected by this module, never user input.
    cursor = conn.execute('SELECT * FROM ' + table)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def read_sequential_layout(conn: sqlite3.Connection) -> SequentialLayout:
    """Read complete schema dimensions in one caller-owned or local snapshot."""
    with _schema_snapshot(conn):
        return _read_layout(conn)


def _read_layout(conn: sqlite3.Connection) -> SequentialLayout:
    """Compare the ENTIRE owned schema against independently built references.

    Observation selects only finite dimensions. No candidate DDL, object filter,
    baseline derivation, table stripping or database mutation is used. Existing
    literal reference construction retains its complete F/E/G/H/I/J semantics.
    """
    _require(not any(str(value).casefold().startswith('workflow_')
                     or str(value).casefold() in ('tasks', 'task_results')
                     for row in conn.execute('SELECT name,tbl_name FROM sqlite_temp_schema')
                     for value in row), 'temporary_shadow_refused')
    actual = _layout(conn)
    names = {row[1] for row in actual[0]}
    sequential = any(str(name).casefold().startswith('workflow_seq_')
                     for row in actual[0] for name in row[1:3])
    if not names:
        return SequentialLayout(None, False, False, 0, False)
    coding = any(str(name).casefold().startswith('workflow_coding_')
                 for row in actual[0] for name in row[1:3])
    document = ('I' if 'workflow_document_revision_operations' in names else
                'H' if 'workflow_author_handoffs' in names else
                'G' if 'workflow_submission_schema_versions' in names else
                'E' if 'workflow_draft_adapter_versions' in names else 'F')
    if document == 'I' and coding:
        document = 'J'
    indexed = any(str(name).casefold().startswith('workflow_owner_') for name in names)
    marked = bool(names & {'workflow_task_ownership', 'workflow_task_owner_bindings'})
    versions = (1, 2) if marked and sequential else (1,) if marked else (0,)
    for ownership in versions:
        with _canonical_connection(document, coding_extension=coding,
                                   owner_indexed=indexed) as expected:
            if sequential:
                _execute_ddl(expected, CANONICAL_WORKFLOW_SEQUENTIAL_DDL)
            if ownership:
                expected.execute(SEQUENTIAL_OWNERSHIP_DDL if ownership == 2 else OWNERSHIP_DDL)
                expected.execute(sequential_binding_view_ddl(document, coding_extension=coding)
                                 if ownership == 2 else binding_view_ddl(document, coding_extension=coding))
            if actual == _layout(expected):
                return SequentialLayout(document, coding, indexed, ownership, sequential)
    raise ValueError('workflow_seq_complete_layout_mismatch')


def _native_contract(conn: sqlite3.Connection) -> None:
    for table, key_type, columns in (
        ('tasks', 'TEXT', {'id', 'parent_task_id', 'assigned_agent', 'current_session_id'}),
        ('task_results', 'INTEGER', {'id', 'task_id', 'agent', 'session_id'}),
    ):
        found = conn.execute('SELECT type,sql FROM sqlite_schema WHERE name=?', (table,)).fetchone()
        metadata = {row[1]: row for row in conn.execute('PRAGMA table_xinfo(' + table + ')')}
        _require(found is not None and found[0] == 'table' and columns <= metadata.keys(), 'native_schema_invalid')
        _require(metadata['id'][2].upper() == key_type and metadata['id'][5] == 1
                 and sum(bool(row[5]) for row in metadata.values()) == 1, 'native_key_invalid')
        if table == 'task_results':
            _require('AUTOINCREMENT' in found[1].upper(), 'native_integer_identity_required')


def _result(conn: sqlite3.Connection, result_id: object, task: str, principal: str, session: str) -> None:
    _require(type(result_id) is int and result_id > 0, 'result_identity_invalid')
    row = conn.execute('SELECT id,task_id,agent,session_id FROM task_results WHERE id=?', (result_id,)).fetchone()
    _require(row is not None and tuple(row) == (result_id, task, principal, session), 'native_result_conflict')


def _definitions(conn: sqlite3.Connection) -> dict[str, CompiledDefinition]:
    result = {}
    for row in _rows(conn, 'workflow_seq_definitions'):
        compiled = compile_definition(_hashed(row['definition_bytes'], row['definition_digest']),
                                      source_digest=row['source_digest'])
        pin = compiled.pin
        _require((row['identity'], row['version'], row['compiler_pin'], row['validator_pin'],
                  row['interpreter_pin'], row['capability_pin']) ==
                 (pin.identity, pin.version, pin.compiler, pin.validator, pin.interpreter, pin.capabilities),
                 'definition_pin_conflict')
        result[row['id']] = compiled
    return result


def _settings(conn: sqlite3.Connection, definitions: dict[str, CompiledDefinition],
              org_slug: str) -> tuple[dict[str, dict], dict[str, StorageSettings]]:
    rows = {row['id']: row for row in _rows(conn, 'workflow_seq_settings')}
    snapshots = {}
    generations: dict[str, list[int]] = {}
    for key, row in rows.items():
        _require(row['org_slug'] == org_slug and row['definition_id'] in definitions, 'settings_owner_invalid')
        snapshot = StorageSettings.model_validate(_json(_hashed(row['snapshot_bytes'], row['snapshot_digest']), 1048576))
        definition = definitions[row['definition_id']]
        bindings = {item.role: item.principal for item in snapshot.bindings}
        _require(snapshot.definition == definition.pin and row['definition_identity'] == definition.pin.identity
                 and len(bindings) == len(snapshot.bindings)
                 and bindings.keys() == dict(definition.definition.roles).keys(), 'settings_pin_conflict')
        for role, requirement in definition.definition.roles:
            _require(all(bindings[role] != bindings[other] for other in requirement.independent_of),
                     'settings_independence_conflict')
        generations.setdefault(row['definition_identity'], []).append(row['generation'])
        snapshots[key] = snapshot
    for values in generations.values():
        _require(sorted(values) == list(range(1, len(values) + 1)), 'settings_generation_gap')
    seen = set()
    for row in _rows(conn, 'workflow_seq_settings_operations'):
        target = rows.get(row['settings_id'])
        _require(target is not None and target['id'] not in seen and row['org_slug'] == org_slug,
                 'settings_operation_conflict')
        request = _record(row['request_bytes'], row['request_digest'], {
            'format', 'operation_key', 'principal', 'settings_id', 'expected_generation', 'snapshot_digest'})
        receipt = _record(row['receipt_bytes'], row['receipt_digest'], {
            'format', 'operation_id', 'settings_id', 'generation', 'snapshot_digest'})
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


def _outputs(conn: sqlite3.Connection, visits: dict[str, dict], runs: dict[str, dict],
             definitions: dict[str, CompiledDefinition]) -> dict[str, list[dict]]:
    selected: dict[str, list[dict]] = {}
    for row in _rows(conn, 'workflow_seq_outputs'):
        visit = visits.get(row['visit_id'])
        _require(visit is not None and visit['run_id'] == row['run_id'] and row['run_id'] in runs,
                 'output_owner_conflict')
        run = runs[row['run_id']]
        definition = definitions[run['definition_id']].definition
        recipients = _json(row['recipients_bytes'], 1048576)
        _require(set(recipients) == {'roles'} and type(recipients['roles']) is list
                 and bool(recipients['roles']) and all(type(role) is str for role in recipients['roles'])
                 and len(set(recipients['roles'])) == len(recipients['roles'])
                 and set(recipients['roles']) <= dict(definition.roles).keys(), 'output_recipients_invalid')
        _hashed(row['content_bytes'], row['content_digest'])
        _require((row['task_id'], row['session_id'], row['result_id'], row['epoch']) ==
                 (visit['task_id'], visit['session_id'], visit['result_id'], visit['epoch']),
                 'output_result_conflict')
        if row['contract'] == 'immutable-document@1':
            row['content_bytes'].decode('utf8')
        else:
            # Domain content integrity only; capability and repository semantics
            # are checked by the future trusted U5 adapter, not unsigned bytes.
            _json(row['content_bytes'], 1048576)
        selected.setdefault(visit['id'], []).append(dict(slot=row['slot'], contract=row['contract'],
            epoch=row['epoch'], producer_visit=visit['id'], task_id=row['task_id'], session_id=row['session_id'],
            result_id=row['result_id'], digest=row['content_digest']))
    return selected


def _event_payload(row: dict) -> None:
    payload = _record(row['event_bytes'], row['event_digest'], {
        'format', 'kind', 'run_id', 'sequence', 'operation_key', 'prior_digest'})
    _require(_bytes(payload) == _bytes(dict(format='workflow-sequential-storage-event@1', kind=row['kind'],
             run_id=row['run_id'], sequence=row['sequence'], operation_key=row['operation_key'],
             prior_digest=row['prior_digest'])), 'event_payload_conflict')


def _selection(visit: dict) -> tuple[OutputRef, ...]:
    value = _record(visit['input_selection_bytes'], visit['input_selection_digest'], {'outputs'})
    _require(type(value['outputs']) is list and len(value['outputs']) <= 32, 'input_selection_invalid')
    result = tuple(OutputRef.model_validate(item) for item in value['outputs'])
    _require(len({item.slot for item in result}) == len(result), 'input_selection_duplicate')
    return result


def _run_history(conn: sqlite3.Connection, run: dict, compiled: CompiledDefinition,
                 settings: dict, snapshot: StorageSettings, visits: list[dict],
                 events: list[dict], outputs: dict[str, list[dict]]) -> None:
    start = parse_start(_hashed(run['start_bytes'], run['start_digest']))
    original = Limits.model_validate(_json(run['original_limits_bytes'], 1048576))
    limits = validate_start(start, compiled, original_limits=original)
    _require(start.operation_key == run['start_operation_key']
             and start.settings.generation == settings['generation']
             and start.settings.snapshot_digest == settings['snapshot_digest']
             and start.bindings == snapshot.bindings and snapshot.admission == 'Allowed'
             and all(getattr(start.lower_limits, key) <= getattr(snapshot.limits, key)
                     for key in Limits.model_fields), 'admission_pin_conflict')
    _result(conn, run['source_result_id'], run['source_task_id'], run['source_principal'], run['source_session_id'])
    source = conn.execute('SELECT assigned_agent FROM tasks WHERE id=?', (run['source_task_id'],)).fetchone()
    carrier = conn.execute('SELECT parent_task_id,current_session_id FROM tasks WHERE id=?', (run['carrier_task_id'],)).fetchone()
    _require(source is not None and source[0] == run['source_principal'] and carrier is not None
             and tuple(carrier) == (run['source_task_id'], None), 'source_carrier_conflict')
    _require([row['ordinal'] for row in visits] == list(range(1, len(visits) + 1))
             and 0 < len(visits) <= limits.max_visits, 'visit_order_invalid')
    _require([row['sequence'] for row in events] == list(range(1, len(events) + 1))
             and 0 < len(events) <= limits.max_events and events[0]['kind'] == 'admitted', 'event_order_invalid')
    nodes = {node.id: node for node in compiled.definition.nodes}
    bindings = {binding.role: binding.principal for binding in start.bindings}
    by_id = {visit['id']: visit for visit in visits}
    for visit in visits:
        node = nodes.get(visit['node_id'])
        _require(node is not None and visit['principal'] == bindings[node.role], 'visit_binding_conflict')
        task = conn.execute('SELECT parent_task_id,assigned_agent FROM tasks WHERE id=?', (visit['task_id'],)).fetchone()
        _require(task is not None and tuple(task) == (run['carrier_task_id'], visit['principal']), 'visit_task_conflict')
        _selection(visit)
        if visit['result_id'] is not None:
            _result(conn, visit['result_id'], visit['task_id'], visit['principal'], visit['session_id'])
            envelope = parse_result(_hashed(visit['result_bytes'], visit['result_digest']))
            checks = _json(visit['verified_checks_bytes'], 1048576)
            _require(set(checks) == {'checks'}, 'verified_checks_invalid')
            AuthenticatedResult(task_id=visit['task_id'], session_id=visit['session_id'],
                result_id=visit['result_id'], envelope=envelope, verified_checks=checks['checks'])
            actual_outputs = sorted(outputs.get(visit['id'], []), key=lambda item: item['slot'])
            _require(actual_outputs == sorted((item.model_dump() for item in envelope.outputs),
                                              key=lambda item: item['slot']), 'output_envelope_conflict')
            _require(envelope.run_id == run['id'] and envelope.visit_id == visit['id']
                     and envelope.definition_digest == compiled.pin.definition_digest
                     and envelope.input_selection_digest == visit['input_selection_digest'], 'result_envelope_conflict')
        else:
            _require(not outputs.get(visit['id']) and visit['state'] not in ('completed', 'failed'), 'visit_result_missing')
    first = visits[0]
    _require(first['node_id'] == compiled.definition.entry_node and first['epoch'] == 1 and not _selection(first)
             and first['predecessor_event_id'] is None, 'entry_visit_invalid')
    counters = Counters(epoch=1, content_returns=0, visits=1, events=0)
    selections, findings, receipts = (), (), ()
    active, state, prior = first, 'active', '0' * 64
    allocated = {first['id']}
    for event in events:
        _require(event['prior_digest'] == prior, 'event_chain_invalid')
        _hashed(event['event_bytes'], event['event_digest'])
        if event['kind'] == 'transition':
            _require(state == 'active' and active is not None and active['result_id'] is not None
                     and event['source_visit_id'] == active['id']
                     and event['source_result_id'] == active['result_id'], 'transition_source_invalid')
            transition = parse_transition(event['event_bytes'])
            envelope = parse_result(active['result_bytes'])
            checks = _json(active['verified_checks_bytes'], 1048576)
            _require(set(checks) == {'checks'}, 'verified_checks_invalid')
            current = ActiveVisit(node=active['node_id'], visit_id=active['id'], task_id=active['task_id'],
                session_id=active['session_id'], epoch=active['epoch'], input_selection_digest=active['input_selection_digest'])
            result = AuthenticatedResult(task_id=active['task_id'], session_id=active['session_id'],
                result_id=active['result_id'], envelope=envelope, verified_checks=checks['checks'])
            decision: Decision = reduce_result(compiled, RunState(run_id=run['id'],
                definition_digest=compiled.pin.definition_digest, original_limits=original,
                lower_limits=start.lower_limits, counters=counters, active_visit=current,
                selections=selections, findings=findings, receipts=receipts), result)
            _require((transition.run_id, transition.definition_digest, transition.source_visit,
                      transition.source_result, transition.result_digest, transition.node, transition.outcome,
                      transition.prior_cas, transition.prior_event_digest, transition.before, transition.after,
                      transition.invalidated, transition.edge) ==
                     (run['id'], compiled.pin.definition_digest, active['id'], active['result_id'],
                      decision.result_digest, active['node_id'], decision.outcome, event['sequence'] - 2,
                      prior, decision.before, decision.after, decision.invalidated, decision.edge),
                     'transition_reducer_conflict')
            digest = envelope.outputs[0].digest if envelope.outputs else None
            _require(transition.output_digest == digest, 'transition_output_conflict')
            counters, selections, findings = decision.after, decision.selections, decision.findings
            receipts += (decision,)
            if isinstance(decision.edge, NextEdge):
                successor = transition.successor
                next_visit = None if successor is None else by_id.get(successor.visit_id)
                _require(next_visit is not None and next_visit['id'] not in allocated
                         and (next_visit['node_id'], next_visit['task_id'], next_visit['epoch'], next_visit['ordinal'],
                              next_visit['predecessor_event_id']) ==
                         (successor.node, successor.task_id, counters.epoch, counters.visits, event['id']),
                         'successor_conflict')
                _require(decision.activation is not None
                         and _selection(next_visit) == decision.activation.inputs, 'successor_input_conflict')
                _require(active['state'] == 'completed' and active['selected'] == 0, 'retired_visit_invalid')
                allocated.add(next_visit['id'])
                active = next_visit
            elif isinstance(decision.edge, TerminalEdge):
                _require(active['state'] == 'completed' and active['selected'] == 0, 'terminal_visit_invalid')
                active, state = None, 'completed'
            elif decision.edge.action == 'fail':
                _require(active['state'] == 'failed' and active['selected'] == 0, 'failed_visit_invalid')
                active, state = None, 'failed'
            else:
                state = 'held'
        else:
            _event_payload(event)
            if event['kind'] == 'admitted':
                _require(event['sequence'] == 1, 'duplicate_admission')
            elif event['kind'] == 'cancelled':
                _require(state in ('active', 'held') and active is not None
                         and active['state'] == 'cancelled' and active['selected'] == 0, 'cancellation_conflict')
                active, state = None, 'cancelled'
            else:
                _require(event['kind'] == 'returned' and state == 'completed'
                         and event is events[-1], 'return_conflict')
        prior = event['event_digest']
    _require(allocated == by_id.keys() and run['state'] == state and run['cas'] == len(events) - 1
             and run['event_count'] == len(events) and run['last_event_id'] == events[-1]['id']
             and (run['epoch'], run['content_returns'], run['visit_count']) ==
             (counters.epoch, counters.content_returns, counters.visits), 'run_history_conflict')
    selected = [visit['id'] for visit in visits if visit['selected']]
    _require(run['active_visit_id'] == (None if active is None else active['id'])
             and selected == ([] if active is None else [active['id']]), 'active_pointer_conflict')
    if state == 'held':
        _require(active is not None and active['state'] == 'held', 'held_visit_invalid')
    elif state == 'active':
        _require(active is not None and active['state'] in ('queued', 'running', 'waiting')
                 and (active['state'] != 'queued' or (active['session_id'] is None and active['result_id'] is None)),
                 'active_visit_invalid')


def _auxiliary(conn: sqlite3.Connection, runs: dict[str, dict], visits: dict[str, dict],
               events: dict[str, dict]) -> None:
    for row in _rows(conn, 'workflow_seq_evidence'):
        _require(row['kind'] in ('preflight', 'review', 'qa', 'intent', 'action', 'observation', 'terminal', 'outer'),
                 'evidence_kind_invalid')
        _require(row['run_id'] in runs and (row['visit_id'] is None or
                 (row['visit_id'] in visits and visits[row['visit_id']]['run_id'] == row['run_id'])),
                 'evidence_owner_conflict')
        _json(_hashed(row['evidence_bytes'], row['evidence_digest']), 1048576)
        if row['result_id'] is not None:
            _result(conn, row['result_id'], row['task_id'], row['principal'], row['session_id'])
            run = runs[row['run_id']]
            visit = visits.get(row['visit_id'])
            _require((visit is not None and row['task_id'] == visit['task_id']) or
                     (row['visit_id'] is None and row['task_id'] == run['source_task_id']), 'evidence_task_conflict')
    notifications: dict[str, dict] = {}
    for row in _rows(conn, 'workflow_seq_outbox'):
        event = events.get(row['event_id'])
        run = runs.get(row['run_id'])
        _require(event is not None and run is not None and event['run_id'] == run['id'], 'outbox_event_conflict')
        payload = _record(row['notification_bytes'], row['notification_digest'], {
            'format', 'notification_id', 'event_id', 'run_id', 'visit_id', 'task_id', 'kind'})
        _require(_bytes(payload) == _bytes(dict(format='workflow-sequential-notification-storage@1', notification_id=row['id'],
                 event_id=event['id'], run_id=run['id'], visit_id=row['visit_id'], task_id=row['task_id'], kind=row['kind'])),
                 'outbox_payload_conflict')
        if row['kind'] == 'visit_queued':
            visit = visits.get(row['visit_id'])
            _require(visit is not None and visit['run_id'] == run['id'] and row['task_id'] == visit['task_id']
                     and ((visit['ordinal'] == 1 and event['kind'] == 'admitted')
                          or visit['predecessor_event_id'] == event['id'])
                     and visit['id'] not in notifications, 'outbox_visit_conflict')
            notifications[visit['id']] = row
        else:
            _require(row['kind'] == 'run_return' and event['kind'] == 'returned'
                     and row['visit_id'] is None and row['task_id'] == run['source_task_id'], 'outbox_return_conflict')
    _require(notifications.keys() == visits.keys(), 'outbox_visit_missing')
    returned = {row['id'] for row in events.values() if row['kind'] == 'returned'}
    actual_returns = {row['event_id'] for row in _rows(conn, 'workflow_seq_outbox') if row['kind'] == 'run_return'}
    _require(returned == actual_returns, 'outbox_return_missing')


def read_sequential_history(conn: sqlite3.Connection, *, expected_org_slug: str) -> SequentialHistory:
    """Validate all sequential rows and complete combined schema in one snapshot.

    Empty/absent storage is explicitly unavailable. A readable inventory is not
    an installed engine, admission, authorization or safe-to-launch disposition.
    Legacy-only layouts still enter their existing complete data reader. When
    sequential storage exists this API validates new history plus old structure;
    U2b must additionally integrate the original old-family data readers.
    """
    _require(type(expected_org_slug) is str and bool(expected_org_slug), 'org_required')
    with _schema_snapshot(conn):
        layout = read_sequential_layout(conn)
        if not layout.sequential:
            if layout.document is not None:
                _read_workflow_schema(conn, expected_org_slug=expected_org_slug)
            return SequentialHistory(layout, 'new-engine unavailable', (), ())
        _native_contract(conn)
        _require([tuple(row) for row in conn.execute('SELECT version FROM workflow_seq_schema_versions')] == [(1,)],
                 'schema_marker_invalid')
        for table in SEQUENTIAL_TABLES:
            _require(conn.execute('PRAGMA foreign_key_check(' + table + ')').fetchone() is None, 'foreign_key_invalid')
            for row in _rows(conn, table):
                for name, value in row.items():
                    if name in ('version', 'generation', 'epoch', 'content_returns', 'visit_count',
                                'event_count', 'cas', 'ordinal', 'sequence', 'selected'):
                        _require(type(value) is int, 'integer_storage_required')
                    elif name.endswith('result_id'):
                        _require(value is None or (type(value) is int and value > 0), 'result_identity_invalid')
                    elif name.endswith(('_id', '_principal')) or name in ('id', 'org_slug', 'principal', 'operation_key', 'start_operation_key'):
                        _require(value is None or (type(value) is str and bool(value) and '\x00' not in value), 'identity_invalid')
        definitions = _definitions(conn)
        settings, snapshots = _settings(conn, definitions, expected_org_slug)
        runs = {row['id']: row for row in _rows(conn, 'workflow_seq_runs')}
        visits = {row['id']: row for row in _rows(conn, 'workflow_seq_visits')}
        events = {row['id']: row for row in _rows(conn, 'workflow_seq_events')}
        outputs = _outputs(conn, visits, runs, definitions)
        _require(not runs or layout.ownership == 2, 'companion_ownership_required')
        for run in runs.values():
            setting = settings.get(run['settings_id'])
            _require(run['org_slug'] == expected_org_slug and run['definition_id'] in definitions
                     and setting is not None and setting['definition_id'] == run['definition_id'], 'run_pin_invalid')
            _run_history(conn, run, definitions[run['definition_id']], setting, snapshots[setting['id']],
                sorted((row for row in visits.values() if row['run_id'] == run['id']), key=lambda row: row['ordinal']),
                sorted((row for row in events.values() if row['run_id'] == run['id']), key=lambda row: row['sequence']), outputs)
        _auxiliary(conn, runs, visits, events)
        if layout.ownership == 2:
            validate_sequential_ownership(conn, org_slug=expected_org_slug)
        elif layout.ownership == 1:
            validate_ownership_data(conn, org_slug=expected_org_slug)
        ready = bool(definitions and settings and layout.ownership == 2)
        return SequentialHistory(layout, 'storage readable' if ready else 'new-engine unavailable',
                                 tuple(sorted(definitions)), tuple(sorted(runs)))
