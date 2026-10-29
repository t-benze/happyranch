"""SELECT-only complete queued K3a closure under the enclosing native budget.

The initial start is reconstructed independently, followed by the closed
ordered edges and native reviewer/QA readers. Unsupported later-stage/control
families refuse.
Saved validity never supplies current access, readiness or permission.
"""
from __future__ import annotations

from runtime.infrastructure._workflow_reader import (
    _add as _reader_add,
    _append as _reader_append,
    _put as _reader_put,
    _setdefault as _reader_setdefault,
    _update as _reader_update,
)

from runtime.infrastructure._workflow_reader import (
    _all_true as _reader_all_true,
)

from runtime.infrastructure._workflow_reader import (
    _dict as _reader_dict,
    _dict_pairs as _reader_dict_pairs,
    _list as _reader_list,
    _set as _reader_set,
    _sorted as _reader_sorted,
    _tuple as _reader_tuple,
)

from runtime.infrastructure._workflow_reader import (
    _decode as _reader_decode,
    _dump as _reader_dump,
    _encode as _reader_encode,
)

from runtime.infrastructure._workflow_reader import (
    _raw_rows as _reader_raw_rows, _raw_one as _reader_raw_one, _json as _reader_json,
    _work as _reader_work, _reserve as _reader_reserve,
)

import json
import sqlite3
import struct
from typing import Any, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from runtime.workflows._coding_settings_execution import _configuration, _resolve_start
from runtime.workflows.coding_authority_history import _Budget, _bounded_rows, _capture_admission, _decoded
from runtime.workflows.coding_bootstrap import (
    decode_row, encode_row, identity, initial_scope, journal_rows, pinned_rows, task_brief,
)
from runtime.workflows._coding_settings_execution import _read_authority, _read_json
from runtime.workflows.coding_saved_records import (
    _canonical, _check, _closed, _hash, _integer, _roles, _timestamp, _token,
)
from runtime.workflows.coding_source_history import (
    _RESULT_TYPES, _TASK_COLUMNS, _TASK_TYPES, _session_start, _task,
)
from runtime.workflows.coding_start_records import effective_mapping, parse_decision, parse_native_decision

# Literal native tables only. This is a row payload boundary, not a schema filter.
PIN_TABLES = (
    'workflow_authorization_revisions', 'workflow_active_authorizations',
    'workflow_binding_snapshots', 'workflow_contexts', 'workflow_instances',
    'workflow_activations', 'workflow_active_activations', 'workflow_activation_operations',
    'workflow_admission_records',
)


def rows(conn: sqlite3.Connection, table: str, budget: _Budget, *,
         where: str = '1', args: tuple = (), maximum: int = 8192) -> Iterator[dict]:
    from runtime.workflows.coding_source_history import _TASK_COLUMNS, _RESULT_COLUMNS
    columns = (_TASK_COLUMNS if table == 'tasks' else _RESULT_COLUMNS if table == 'task_results'
               else _reader_tuple((row[1] for row in _reader_raw_rows(conn, f'PRAGMA table_xinfo("{table}")', budget=budget)), budget=budget))
    allowed = (_TASK_TYPES if table == 'tasks' else _RESULT_TYPES if table == 'task_results'
               else (('text', 'blob', 'integer', 'real', 'null'),) * len(columns))
    for row in _bounded_rows(conn, table, columns, allowed, budget,
                             where=where, args=args, maximum=maximum,
                             value_limit=16777216 if table == 'workflow_coding_events' else 1048576):
        _reader_reserve(budget, 128 * len(columns) + 64)
        yield _reader_dict(zip(columns, row[:len(columns)], strict=True), budget=budget)


def same(left: Any, right: Any, *, budget=None) -> bool:
    # Charge each actual comparison before its traversal/temporary allocation.
    # Recursion forwards this same allowance; no validated-row exemption exists.
    _reader_reserve(budget, 128)
    if budget is not None and type(left) is dict and type(right) is dict:
        _reader_reserve(budget, 64 * (len(left) + len(right)))
        for key in left:
            _reader_work(key, budget)
        for key in right:
            _reader_work(key, budget)
    elif budget is not None and (type(left) in (_reader_reserve(budget, 96) or (str, bytes)) or type(right) in (_reader_reserve(budget, 96) or (str, bytes))):
        _reader_work((_reader_reserve(budget, 96) or (left, right)), budget)
    if type(left) is float and type(right) is float:
        return struct.pack('>d', left) == struct.pack('>d', right)
    return type(left) is type(right) and (
        left.keys() == right.keys() and _reader_all_true((same(left[k], right[k], budget=budget) for k in left), budget=budget)
        if type(left) is dict else
        len(left) == len(right) and _reader_all_true((same(a, b, budget=budget) for a, b in zip(left, right, strict=True)), budget=budget)
        if type(left) is list else left == right)


def claim_for(row: dict, result_id: int) -> dict:
    return dict(task_id=row['id'], assigned_agent=row['assigned_agent'], team=row['team'],
        current_session_id=row['current_session_id'], step=row['orchestration_step_count'],
        active_chain=row['active_chain'], active_fanout=row['active_fanout'], revision=row['revision_count'],
        note=row['note'], status=row['status'], block_kind=row['block_kind'],
        cancelled_at=row['cancelled_at'], result_row_id=result_id)


@dataclass
class QueuedStorage:
    admitted: dict = field(default_factory=dict)
    runs: dict = field(default_factory=dict)
    owners: dict = field(default_factory=dict)
    callbacks: dict = field(default_factory=dict)
    cancellations: dict = field(default_factory=dict)
    cancellation_comparisons: list = field(default_factory=list)
    cancellation_dependencies: set = field(default_factory=set)
    # Provisional per-graph cutoff; it grants nothing until full return replay.
    return_boundary: str | None = None

    def add(self, table: str, row: dict, *, budget=None) -> None:
        _reader_reserve(budget, 256)
        key = _canonical(encode_row(row, budget=budget), reader_budget=budget)
        _reader_work(key, budget)
        _reader_add(_reader_setdefault(self.admitted, table, _reader_set(budget=budget), budget=budget), key, budget=budget)

    def matches(self, table: str, row: dict, *, budget=None) -> bool:
        key = _canonical(encode_row(row, budget=budget), reader_budget=budget)
        _reader_work(key, budget)
        return key in self.admitted.get(table, ())


def _reserved_rows(conn, table, column, budget, *, where, args=()):
    from runtime.infrastructure.workflow_coding_schema import _coding_metadata_candidate
    for rowid, raw in _reader_raw_rows(conn, f'SELECT rowid,{column} FROM {table} WHERE ' + where
                                   + ' ORDER BY rowid', args, budget=budget):
        if _coding_metadata_candidate(raw, location=f'{table}.{column}', budget=budget):
            yield from rows(conn, table, budget, where='rowid=?', args=(_reader_reserve(budget, 80) or (rowid,)), maximum=1)


def read_queued(conn: sqlite3.Connection, org: str, budget: _Budget, mappings: Any, imported: Any) -> QueuedStorage:
    """Independent queued-history entry retains its complete structure check."""
    if not conn.in_transaction:
        raise ValueError('workflow_coding_exclusion_requires_snapshot')
    from runtime.infrastructure.workflow_schema import _read_structure
    layout, _ = _read_structure(conn, budget=budget)
    return _read_queued_data(conn, org, budget, mappings, imported, layout=layout)


def _read_queued_data(conn: sqlite3.Connection, org: str, budget: _Budget,
                      mappings: Any, imported: Any, *, layout: str) -> QueuedStorage:
    """Full data/history reconstruction below the actual structural owner.

    Layout selects only the literal optional document tables. It certifies no
    row, callback, native proof or inventory, and never replaces their complete
    validators. All acquisitions and reconstruction use the original remainder.
    """
    if not conn.in_transaction:
        raise ValueError('workflow_coding_exclusion_requires_snapshot')
    from runtime.infrastructure.workflow_schema import _coding_document_tables
    optional = _coding_document_tables(layout, budget=budget)
    storage = QueuedStorage()
    # Discriminate ordinary decision prose before native acquisition. The same
    # lexical/decoded inventory retains escaped, duplicate and malformed reserved
    # candidates; central closure still refuses unsupported envelopes.
    for row in _reserved_rows(conn, 'task_results', 'decision_json', budget,
                              where='decision_json IS NOT NULL'):
        raw = row['decision_json']
        if raw is None:
            continue
        try:
            _reader_reserve(budget, 96)
            envelope = _reader_json(raw, budget)
        except ((json.JSONDecodeError, TypeError)):
            continue  # Central lexical discriminator still refuses malformed coding.
        if type(envelope) is not dict or envelope.get('action') != 'workflow_start':
            continue
        _decoded(envelope, budget)
        decision = parse_native_decision(_reader_encode(raw, 'utf8', budget=budget), budget=budget)
        if decision['workflow']['format'] == 'workflow-coding-start@2':
            _resolve_start(decision['workflow'], mappings, imported, budget)
        _integer(row['id'], 1, reader_budget=budget)
        _check(row['status'] == 'completed' and row['waiting_on_job_ids'] is None)
        issuer = (_reader_reserve(budget, 448) or dict(task_id=row['task_id'], session_id=row['session_id'], principal=row['agent']))
        _session_start(conn, issuer, budget)
        native = _task(conn, row['task_id'], budget)
        _check(native._text('task_type', budget=budget) == 'task')
        _reader_put((_reader_reserve(budget, 96) or (row, decision)), storage.callbacks, row['id'], budget=budget)
        storage.add('task_results', row, budget=budget)
    if storage.callbacks:
        from runtime.orchestrator.orchestrator import completion_report_from_result_row
        task_ids = _reader_tuple(_reader_sorted(_reader_set((r['task_id'] for r, _ in storage.callbacks.values()), budget=budget), budget=budget), budget=budget)
        selection = "action='completion_report' AND task_id IN (" + ','.join('?' for _ in task_ids) + ')'
        for audit in _reserved_rows(conn, 'audit_log', 'payload', budget, where=selection, args=task_ids):
            if audit['payload'] is None:
                continue
            try:
                body = _reader_json(audit['payload'], budget)
            except json.JSONDecodeError:
                continue
            if type(body) is not dict or type(body.get('decision')) is not dict or body['decision'].get('action') != 'workflow_start':
                continue
            _decoded(body, budget)
            candidates = (_reader_reserve(budget, 64) or [])
            for callback, _ in storage.callbacks.values():
                budget.step()
                expected = _reader_dump(completion_report_from_result_row(callback['task_id'], callback), budget=budget)
                if '_result_row_id' in body or '_result_session_id' in body:
                    _reader_update(expected, (_reader_reserve(budget, 320) or {'_result_row_id': callback['id'], '_result_session_id': callback['session_id']}), budget=budget)
                if (_reader_reserve(budget, 96) or (audit['task_id'], audit['agent'])) == (_reader_reserve(budget, 96) or (callback['task_id'], callback['agent'])) and same(expected, body, budget=budget):
                    _reader_append(candidates, callback, budget=budget)
            _check(len(candidates) == 1)
            storage.add('audit_log', audit, budget=budget)
    run_rows = _reader_list(rows(conn, 'workflow_coding_runs', budget), budget=budget)
    attempts = _reader_list(rows(conn, 'workflow_coding_attempts', budget), budget=budget)
    events = _reader_list(rows(conn, 'workflow_coding_events', budget), budget=budget)
    _check(len(run_rows) <= len(attempts) <= 10 * len(run_rows) and len(events) >= len(run_rows))
    used_attempts, used_events, used_sources = (_reader_reserve(budget, 112) or (_reader_set(budget=budget), _reader_set(budget=budget), _reader_set(budget=budget)))
    for run in run_rows:
        budget.step()
        _check(imported is not None and mappings is not None)
        instance = run['instance_id']
        selected = _reader_list((r for r in events if r['instance_id'] == instance), budget=budget)
        selected_attempts = _reader_list((r for r in attempts if r['instance_id'] == instance), budget=budget)
        selected.sort(key=lambda row: row['event_seq'])
        _check(len(selected) >= 1 and 1 <= len(selected_attempts) <= 10)
        event = selected[0]
        actual_run = run
        initial = _read_json(event['event_bytes'], budget, 16777216)
        run = initial['after']['run']
        attempt = decode_row(initial['after']['attempts'][0]['record'], budget=budget)
        _check(event['id'] not in used_events and attempt['id'] not in used_attempts)
        for selected_event in selected:
            _check(selected_event['id'] not in used_events)
            _reader_add(used_events, selected_event['id'], budget=budget)
        _check(type(event['event_bytes']) is bytes and _hash(event['event_bytes'], reader_budget=budget) == event['event_digest'])
        payload = _closed(_read_json(event['event_bytes'], budget, 16777216),
            'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at', reader_budget=budget)
        _check(payload['format'] == 'workflow-coding-event@1'
               and payload['event_kind'] == 'run_started' and payload['event_seq'] == 1
               and payload['previous_digest'] is None)
        _check(_canonical(payload, reader_budget=budget) == event['event_bytes'])
        for key in ('id', 'instance_id', 'event_seq', 'event_kind', 'attempt_id', 'previous_digest', 'created_at'):
            _check(same(payload[key], event[key], budget=budget))
        _timestamp(payload['created_at'], reader_budget=budget)
        evidence = _closed(payload['evidence'], 'kind command origin authority rows facts native_task_observations', reader_budget=budget)
        _check(evidence['kind'] == 'run_started' and evidence['native_task_observations'] == (_reader_reserve(budget, 64) or []))
        decision = parse_decision(_canonical((_reader_reserve(budget, 320) or dict(action='workflow_start', workflow=evidence['command'])), reader_budget=budget), budget=budget)
        command = decision['workflow']
        digest = _hash(_canonical(command, reader_budget=budget), reader_budget=budget)
        operation = (_reader_reserve(budget, 576) or dict(org_slug=org, principal=event['principal'], key=command['operation_key'], request_digest=digest))
        _check(payload['operation'] == operation and event['org_slug'] == org
               and event['operation_key'] == operation['key'] and event['request_digest'] == digest
               and event['id'] == identity('event', org, instance, event['principal'], operation['key'], budget=budget))
        facts = _closed(evidence['facts'], 'source carrier_task_id release mapping_id mapping_digest context_id '
                         'binding_snapshot_id authorization_id activation_id first_attempt_id', reader_budget=budget)
        source = _closed(facts['source'], 'task_id session_id principal result result_claim parent_task_id root_ancestor_id budgets', reader_budget=budget)
        result = _closed(source['result'], 'id record', reader_budget=budget)
        _integer(result['id'], 1, reader_budget=budget)
        _check(result['id'] in storage.callbacks and source['task_id'] not in used_sources)
        _reader_add(used_sources, source['task_id'], budget=budget)
        callback, stored_decision = storage.callbacks[result['id']]
        _check(same(result['record'], encode_row(callback, budget=budget), budget=budget) and stored_decision == decision
               and (_reader_reserve(budget, 112) or (callback['task_id'], callback['agent'], callback['session_id'])) ==
                   (_reader_reserve(budget, 112) or (source['task_id'], source['principal'], source['session_id'])))
        _check(event['principal'] == 'agent:' + source['principal']
               and instance == identity('root', org, source['task_id'], result['id'], digest, budget=budget))
        origin = _closed(evidence['origin'], 'kind principal task_id session_id result_id claim', reader_budget=budget)
        _check(origin == (_reader_reserve(budget, 832) or dict(kind='manager_result', principal=source['principal'], task_id=source['task_id'],
                             session_id=source['session_id'], result_id=result['id'], claim=None)))
        claim = _closed(source['result_claim'], 'native_claim owner_invocation_id action_fingerprint', reader_budget=budget)
        _token(claim['owner_invocation_id'], reader_budget=budget)
        _check(claim['action_fingerprint'] == _hash(_reader_encode(callback['decision_json'], 'utf8', budget=budget), reader_budget=budget))
        before = _closed(payload['before'], 'run attempts instances tasks rounds requests assignments', reader_budget=budget)
        after = _closed(payload['after'], 'run attempts instances tasks rounds requests assignments', reader_budget=budget)
        _check(before['run'] is None and same(after['run'], run, budget=budget))
        for name in ('rounds', 'requests', 'assignments'):
            _check(before[name] == after[name] == (_reader_reserve(budget, 64) or []))
        _check(type(after['tasks']) is list and len(after['tasks']) == len(before['tasks']) == 3)
        after_tasks = _reader_dict_pairs(((_reader_reserve(budget, 96) or (r['id'],  decode_row(r['record'], budget=budget) )) for r in after['tasks']), budget=budget)
        before_tasks = _reader_dict_pairs(((_reader_reserve(budget, 96) or (r['id'],  None if r['record'] is None else decode_row(r['record'], budget=budget) )) for r in before['tasks']), budget=budget)
        _check(len(after_tasks) == 3 and after_tasks.keys() == before_tasks.keys())
        source_before = before_tasks[source['task_id']]
        _check(type(source_before) is dict and _reader_tuple(source_before, budget=budget) != ()
               and _reader_set(source_before, budget=budget) == _reader_set(_TASK_COLUMNS, budget=budget)
               and same(claim['native_claim'], claim_for(source_before, result['id']), budget=budget))
        _check(source_before['status'] == 'in_progress' and source_before['task_type'] == 'task'
               and source_before['assigned_agent'] == source['principal']
               and source_before['current_session_id'] == source['session_id'])
        _check(source_before['completed_at'] is None and source_before['zombie_flagged_at'] is None)
        for key in ('block_kind', 'active_chain', 'active_fanout', 'blocked_on_job_ids', 'cancelled_at'):
            _check(source_before[key] is None)
        expected_source = {**source_before, 'block_kind': 'delegated', 'note': 'coding-owned:' + instance,
                           'updated_at': payload['created_at']}
        _check(same(after_tasks[source['task_id']], expected_source, budget=budget))
        pin_entries = _reader_list((e for e in mappings.mappings if e.mapping_id == command['mapping']['id']), budget=budget)
        _check(len(pin_entries) == 1)
        mapping_entry = pin_entries[0]
        _check(command['mapping'] == (_reader_reserve(budget, 448) or dict(id=mapping_entry.mapping_id, generation=mapping_entry.generation,
                                         digest=mapping_entry.mapping_digest)) and run['mapping_id'] == mapping_entry.mapping_id)
        record = _read_json(mapping_entry.mapping_bytes, budget, 1048576)
        settings = _resolve_start(command, mappings, imported, budget)
        mapping = record['mapping']
        effective = effective_mapping(mapping, command, budget=budget)
        roles = effective['roles']
        _roles(roles, _reader_dict_pairs(((_reader_reserve(budget, 96) or (k,  (_reader_reserve(budget, 64) or []) )) for k in roles), budget=budget), reader_budget=budget)
        _check(roles['agent_manager'] == (_reader_reserve(budget, 320) or dict(principal=source['principal'], team=source_before['team'])))
        grant, snapshot = _read_authority(evidence['authority'], org, budget)
        _check(grant == _canonical(command, reader_budget=budget))
        _configuration(mapping, snapshot, command, budget)
        _configuration(effective, snapshot, command, budget)
        carrier_id = facts['carrier_task_id']
        _check(carrier_id in after_tasks and carrier_id != source['task_id'])
        carrier, leaf = (_reader_reserve(budget, 96) or (after_tasks[carrier_id], after_tasks[attempt['task_id']]))
        _check(carrier['parent_task_id'] == source['task_id'] and leaf['parent_task_id'] == carrier_id)
        for row, binding, state, block, brief in (_reader_reserve(budget, 96) or (
            (_reader_reserve(budget, 144) or (carrier, roles['agent_manager'], 'in_progress', 'delegated', 'Structural coding carrier ' + instance)),
            (_reader_reserve(budget, 144) or (leaf, roles['implementer'], 'pending', None, task_brief(command['work'], budget=budget))),
        )):
            _check(_reader_set(row, budget=budget) == _reader_set(_TASK_COLUMNS, budget=budget) and row['task_type'] == 'subtask'
                   and row['assigned_agent'] == binding['principal'] and row['team'] == binding['team']
                   and row['status'] == state and row['block_kind'] == block and row['brief'] == brief
                   and row['revision_count'] == 0 and row['orchestration_step_count'] == 0
                   and row['created_at'] == row['updated_at'] == payload['created_at']
                   and row['session_timeout_seconds'] == source_before['session_timeout_seconds'])
            for key in ('revisit_of_task_id', 'dispatched_from_thread_id', 'completed_at', 'final_output_summary',
                        'final_output_dir', 'executor_pid', 'note', 'cancelled_at', 'last_heartbeat',
                        'blocked_on_job_ids', 'active_chain', 'active_fanout', 'current_session_id', 'zombie_flagged_at'):
                _check(row[key] is None)
            _check(before_tasks[row['id']] is None)
        # Context contains only this new version's native source capture. The
        # original session-start and all ancestor rows retain their exact bytes.
        _check(type(evidence['rows']) is list and len(evidence['rows']) == len(PIN_TABLES))
        context_rows = _reader_list((r for r in evidence['rows'] if r.get('table') == 'workflow_contexts'), budget=budget)
        _check(len(context_rows) == 1)
        frozen_context = decode_row(context_rows[0]['record'], budget=budget)
        context_value = _read_json(frozen_context['context_bytes'], budget, 1048576)
        native_source = _closed(context_value['native_source'], 'ancestors session_start invocation checkpoints', reader_budget=budget)
        checkpoints = native_source['checkpoints']
        _check(type(checkpoints) is list and 1 <= len(checkpoints) <= 5)
        from runtime.workflows.coding_source_history import _timestamp as native_timestamp
        baseline = None
        for observed in checkpoints:
            _closed(observed, 'source ancestors', reader_budget=budget)
            _check(type(observed['ancestors']) is list and len(observed['ancestors']) < 20)
            records = [decode_row(observed['source'], budget=budget), *_reader_list((decode_row(r, budget=budget) for r in observed['ancestors']), budget=budget)]
            for record in records:
                _check(_reader_set(record, budget=budget) == _reader_set(_TASK_COLUMNS, budget=budget))
                native_timestamp(record['updated_at'])
                native_timestamp(record['last_heartbeat'], nullable=True)
            if baseline is not None:
                _check(len(records) == len(baseline))
                for old, new in zip(baseline, records, strict=True):
                    # Pre-bootstrap native clocks are observations. Retain BOTH;
                    # all other 25 fields must agree. No post-start overlay is
                    # admitted without the later native writer/queue closure.
                    for name in _TASK_COLUMNS:
                        if name not in ('last_heartbeat', 'updated_at'):
                            _check(same(old[name], new[name], budget=budget))
            baseline = records
        _check(same(baseline[0], source_before, budget=budget)
               and same(checkpoints[-1]['ancestors'], native_source['ancestors'], budget=budget))
        chain = (_reader_reserve(budget, 192) or [source_before])
        seen = _reader_set((_reader_reserve(budget, 80) or (source['task_id'],)), budget=budget)
        _check(type(native_source['ancestors']) is list and len(native_source['ancestors']) < 20)
        for encoded in native_source['ancestors']:
            parent = decode_row(encoded, budget=budget)
            _check(_reader_set(parent, budget=budget) == _reader_set(_TASK_COLUMNS, budget=budget) and chain[-1]['parent_task_id'] == parent['id']
                   and parent['id'] not in seen and parent['team'] == source_before['team']
                   and parent['cancelled_at'] is None
                   and parent['status'] not in ('failed', 'completed', 'cancelled', 'superseded'))
            _reader_add(seen, parent['id'], budget=budget)
            _reader_append(chain, parent, budget=budget)
        _check(chain[-1]['parent_task_id'] is None and source['root_ancestor_id'] == chain[-1]['id']
               and source['parent_task_id'] == source_before['parent_task_id'])
        # Current ancestry may have native observations, but no owner/edge drift.
        current_ancestors = (_reader_reserve(budget, 64) or [])
        for historical in chain[1:]:
            current = _task(conn, historical['id'], budget)
            actual = _reader_dict_pairs(((_reader_reserve(budget, 96) or (c.name,  _reader_decode(c.value, 'utf8', budget=budget) if c.storage_class == 'text' else c.value )) for c in current.columns), budget=budget)
            _reader_append(current_ancestors, (_reader_reserve(budget, 96) or (actual, historical)), budget=budget)
        start = _session_start(conn, (_reader_reserve(budget, 448) or dict(task_id=source['task_id'], session_id=source['session_id'], principal=source['principal'])), budget)
        actual_start = _reader_dict_pairs(((_reader_reserve(budget, 96) or (c.name,  _reader_decode(c.value, 'utf8', budget=budget) if c.storage_class == 'text' else c.value )) for c in start.columns), budget=budget)
        _check(same(native_source['session_start'], actual_start, budget=budget))
        invocation = _closed(native_source['invocation'], 'id owner entry', reader_budget=budget)
        _check(invocation['id'] == claim['owner_invocation_id'] and invocation['owner'] in ('ordinary', 'v2'))
        from runtime.infrastructure.task_pause_controls import decode_journal
        entry = invocation['entry']
        decode_journal(_reader_decode(_canonical((_reader_reserve(budget, 320) or dict(version=1, entries=(_reader_reserve(budget, 192) or [entry]))), reader_budget=budget), 'utf8', budget=budget), budget=budget)
        # Validate original admission against the frozen source, independently
        # of reconstructed pins or today's mutable native invocation lifecycle.
        _check(not entry['context'].get('execution_unknown'))
        _check(entry['fingerprint']['brief_digest'] == _hash(_reader_encode(source_before['brief'], 'utf8', budget=budget), reader_budget=budget))
        _check(entry['id'] == invocation['id'] and entry['owner'] == invocation['owner']
               and entry['task_id'] == source['task_id'] and entry['session_id'] == source['session_id']
               and entry['agent'] == source['principal'] and entry['phase'] == 'running'
               and entry['root_task_id'] == source['root_ancestor_id'] and entry['org'] == org
               and entry['fingerprint']['count'] + 1 == source_before['orchestration_step_count'])
        limits = _closed(source['budgets'], 'source_step source_revision session_timeout_seconds max_revise_rounds '
                         'coding_attempt_limit coding_event_limit coding_revision_limit coding_same_principal_retry_limit', reader_budget=budget)
        for key, value in limits.items():
            if value is not None:
                _integer(value, reader_budget=budget)
        _check(limits['source_step'] == source_before['orchestration_step_count']
               and limits['source_revision'] == source_before['revision_count']
               and limits['session_timeout_seconds'] == source_before['session_timeout_seconds']
               and limits['coding_attempt_limit'] == 64 and limits['coding_event_limit'] == 4096
               and limits['coding_revision_limit'] == min(3, limits['max_revise_rounds'] - limits['source_revision'] if limits['max_revise_rounds'] else 3)
               and limits['coding_same_principal_retry_limit'] == 1)
        template = dict(identity_id=imported.identity_id, version_id=imported.version_id,
                        **command['release'])
        expected_pins, context, _ = pinned_rows(org=org, command=command, source=source,
            native_source=native_source, authority=evidence['authority'], mapping=mapping, roles=roles,
            template=template, carrier_id=carrier_id, instance_id=instance, created_at=payload['created_at'], budget=budget)
        _check(same(evidence['rows'], journal_rows(expected_pins, budget=budget), budget=budget))
        _capture_admission(conn, expected_pins['workflow_admission_records'][0], evidence['authority'], budget)
        _check(len(selected_attempts) <= limits['coding_attempt_limit'])
        scope = _canonical(initial_scope(org=org, instance=instance, attempt=attempt['id'], command=command,
                                        roles=roles, context=context, budget=budget), reader_budget=budget)
        _check(same(attempt, (_reader_reserve(budget, 2112) or dict(id=identity('attempt', instance, 1, budget=budget), instance_id=instance,
            revision=1, stage='development', attempt_sequence=1, assignment_generation=1,
            predecessor_id=None, task_id=leaf['id'], principal=roles['implementer']['principal'], session_id=None,
            result_id=None, submission_id=None, review_request_id=None, state='queued',
            scope_bytes=scope, scope_digest=_hash(scope, reader_budget=budget))), budget=budget))
        _check(same(run, (_reader_reserve(budget, 960) or dict(instance_id=instance, mapping_id=mapping_entry.mapping_id, cas_version=1,
            current_revision=0, state='development', current_attempt_id=attempt['id'], last_event_id=event['id'])), budget=budget))
        _check(facts == (_reader_reserve(budget, 1344) or dict(source=source, carrier_task_id=carrier_id, release=command['release'],
            mapping_id=mapping_entry.mapping_id, mapping_digest=mapping_entry.mapping_digest,
            context_id=identity('context', instance, budget=budget), binding_snapshot_id=identity('binding', instance, budget=budget),
            authorization_id=identity('authorization', instance, budget=budget), activation_id=identity('activation', instance, budget=budget),
            first_attempt_id=attempt['id'])))
        for name, values in (_reader_reserve(budget, 96) or (_reader_reserve(budget, 96) or (('attempts', _reader_reserve(budget, 192) or [attempt]), _reader_reserve(budget, 96) or ('instances', expected_pins['workflow_instances'])))):
            key = 'id'
            _check(before[name] == _reader_list(((_reader_reserve(budget, 320) or dict(id=r[key], record=None)) for r in values), budget=budget)
                   and after[name] == _reader_list(((_reader_reserve(budget, 320) or dict(id=r[key], record=encode_row(r, budget=budget))) for r in values), budget=budget))
        for table, values in expected_pins.items():
            for expected in values:
                key = 'id' if 'id' in expected else 'namespace' if 'namespace' in expected else 'instance_id' if 'instance_id' in expected else 'activation_id'
                actual = _reader_list(rows(conn, table, budget, where=f'{key}=?', args=(_reader_reserve(budget, 80) or (expected[key],)), maximum=2), budget=budget)
                _check(len(actual) == 1 and same(actual[0], expected, budget=budget))
                storage.add(table, expected, budget=budget)
        # Reverse references are part of ownership too: a second legacy
        # instance/context/binding/activation cannot alias these new pins just
        # because its own scalar columns contain no coding discriminator.
        for table, column, key in (_reader_reserve(budget, 192) or (
            (_reader_reserve(budget, 112) or ('workflow_active_authorizations', 'authorization_revision_id', identity('authorization', instance, budget=budget))),
            (_reader_reserve(budget, 112) or ('workflow_binding_snapshots', 'authorization_revision_id', identity('authorization', instance, budget=budget))),
            (_reader_reserve(budget, 112) or ('workflow_contexts', 'binding_snapshot_id', identity('binding', instance, budget=budget))),
            (_reader_reserve(budget, 112) or ('workflow_instances', 'binding_snapshot_id', identity('binding', instance, budget=budget))),
            (_reader_reserve(budget, 112) or ('workflow_instances', 'context_id', identity('context', instance, budget=budget))),
            (_reader_reserve(budget, 112) or ('workflow_activations', 'instance_id', instance)),
            (_reader_reserve(budget, 112) or ('workflow_active_activations', 'activation_id', identity('activation', instance, budget=budget))),
            (_reader_reserve(budget, 112) or ('workflow_activation_operations', 'activation_id', identity('activation', instance, budget=budget))),
        )):
            actual = _reader_list(rows(conn, table, budget, where=f'{column}=?', args=(_reader_reserve(budget, 80) or (key,)), maximum=2), budget=budget)
            _check(same(actual, expected_pins[table], budget=budget))
        from runtime.workflows.coding_native_history import read_native
        returned = _reader_list((e for e in selected[1:] if e['event_kind'] == 'return'), budget=budget)
        _check(len(returned) <= 1)
        storage.return_boundary = None if not returned else returned[0]['created_at']
        comparison_start = len(storage.cancellation_comparisons)
        native = read_native(conn, org, budget, storage, start=event, events=selected[1:],
            tasks=after_tasks, source=source, roles=roles, authority=evidence['authority'],
            command_value=command, mapping=mapping, source_invocation=entry,
            source_ancestors=_reader_list((decode_row(r, budget=budget) for r in native_source['ancestors']), budget=budget))
        # Another graph may carry a later ordinary-ancestor cancellation.
        # Resolve changed equality only after ALL independent batches close.
        from runtime.workflows._coding_cancellation_history import defer_ancestry
        _check(bool(native.get('returned')) is bool(returned))
        for actual, historical in current_ancestors:
            defer_ancestry(actual, historical, storage, budget)
        if returned:
            # This graph relinquished ordinary callers at its authenticated
            # return. Their later native state is not coding-owned; original
            # captures/predicates remain fully checked at each historical edge.
            comparisons = storage.cancellation_comparisons[comparison_start:]
            for historical in comparisons:
                actual = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (historical['id'],)), maximum=1), budget=budget)
                _check(len(actual) == 1)
                current = actual[0]
                for key in ('id', 'task_type', 'parent_task_id', 'revisit_of_task_id', 'team',
                            'assigned_agent', 'brief', 'created_at', 'dispatched_from_thread_id'):
                    _check(same(current[key], historical[key], budget=budget))
                _check(datetime.fromisoformat(current['updated_at']) >=
                       datetime.fromisoformat(storage.return_boundary.replace('Z', '+00:00')))
            del storage.cancellation_comparisons[comparison_start:]
        storage.return_boundary = None
        actual_attempts = _reader_dict_pairs(((_reader_reserve(budget, 96) or (a['id'],  a )) for a in selected_attempts), budget=budget)
        _check(len(actual_attempts) == len(selected_attempts)
               and same(native['run'], actual_run, budget=budget) and same(native['attempts'], actual_attempts, budget=budget))
        _check(not (_reader_set(actual_attempts, budget=budget) & used_attempts))
        _reader_update(used_attempts, actual_attempts, budget=budget)
        actual_attempt = actual_attempts[actual_run['current_attempt_id']]
        after_tasks = native['tasks']
        run, attempt = (_reader_reserve(budget, 96) or (actual_run, actual_attempt))
        for task_id, expected in after_tasks.items():
            actual = _reader_list(rows(conn, 'tasks', budget, where='id=?', args=(_reader_reserve(budget, 80) or (task_id,)), maximum=1), budget=budget)
            _check(len(actual) == 1 and same(actual[0], expected, budget=budget))
            storage.add('tasks', expected, budget=budget)
            roots = _reader_list(rows(conn, 'workflow_instances', budget, where='root_task_id=?', args=(_reader_reserve(budget, 80) or (task_id,)), maximum=2), budget=budget)
            _check(same(roots, expected_pins['workflow_instances'] if task_id == carrier_id else (_reader_reserve(budget, 64) or []), budget=budget))
        # No alternate dispatcher, result, submission, assignment or premature
        # downstream stage may share this closed queued graph.
        for table, column, keys in (_reader_reserve(budget, 304) or (
            (_reader_reserve(budget, 112) or ('workflow_draft_dispatch_intents', 'task_id', after_tasks)),
            (_reader_reserve(budget, 112) or ('workflow_request_task_bridges', 'task_id', after_tasks)),
            (_reader_reserve(budget, 112) or ('workflow_instance_tasks', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_task_results', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_current_assignments', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_instance_contributors', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_dispatch_operations', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_operation_replays', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_author_handoffs', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_document_revision_operations', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_dispatch_callbacks', 'task_id', after_tasks)),
            (_reader_reserve(budget, 112) or ('workflow_submissions', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_rounds', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('workflow_events', 'instance_id', (_reader_reserve(budget, 192) or [instance]))),
            (_reader_reserve(budget, 112) or ('task_results', 'task_id', (_reader_reserve(budget, 192) or [carrier_id]))),
        )):
            if table in ('workflow_draft_dispatch_intents', 'workflow_author_handoffs',
                         'workflow_document_revision_operations') and table not in optional:
                continue
            for key in keys:
                budget.step()
                _check(_reader_raw_one(conn, f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1', (_reader_reserve(budget, 80) or (key,)), budget=budget) is None)
        for owned_attempt in actual_attempts.values():
            owned_state = native['stages'][owned_attempt['id']]
            callbacks = _reader_list(rows(conn, 'task_results', budget, where='task_id=?', args=(_reader_reserve(budget, 80) or (owned_attempt['task_id'],))), budget=budget)
            _check(same(callbacks, (_reader_reserve(budget, 64) or []) if owned_state['callback'] is None else (_reader_reserve(budget, 192) or [owned_state['callback']]), budget=budget))
            budget.step()
            _check(_reader_raw_one(conn, 'SELECT 1 FROM tasks WHERE parent_task_id=? LIMIT 1', (_reader_reserve(budget, 80) or (owned_attempt['task_id'],)), budget=budget) is None)
            storage.add('workflow_coding_attempts', owned_attempt, budget=budget)
        for child in rows(conn, 'tasks', budget, where='parent_task_id=?', args=(_reader_reserve(budget, 80) or (source['task_id'],))):
            _check(child['id'] == carrier_id or (child['status'] in ('completed', 'failed', 'cancelled', 'superseded')
                and datetime.fromisoformat(child['created_at']) <= datetime.fromisoformat(payload['created_at'].replace('Z', '+00:00'))))
        _check(_reader_set((r['id'] for r in rows(conn, 'tasks', budget, where='parent_task_id=?',
                                    args=(_reader_reserve(budget, 80) or (carrier_id,)), maximum=10)), budget=budget) == _reader_set((a['task_id'] for a in actual_attempts.values()), budget=budget))
        for table, row in (_reader_reserve(budget, 112) or ((_reader_reserve(budget, 96) or ('workflow_coding_runs', run)), (_reader_reserve(budget, 96) or ('workflow_coding_attempts', attempt)), (_reader_reserve(budget, 96) or ('workflow_coding_events', event)))):
            storage.add(table, row, budget=budget)
        _reader_put(_reader_reserve(budget, 1728) or dict(run=run, command=command, roles=roles, attempt=attempt, event=event,
                                    source=source, attempts=actual_attempts, mapping=mapping, binding=expected_pins['workflow_binding_snapshots'][0],
                                    native=native, authority=evidence['authority'], tasks=after_tasks, settings=settings), storage.runs, instance, budget=budget)
        for task_id, kind in (_reader_reserve(budget, 128 + 32 * (2 + len(actual_attempts.values()))) or ((_reader_reserve(budget, 96) or (source['task_id'], 'coding_source_waiting')),
                              (_reader_reserve(budget, 96) or (carrier_id, 'coding_carrier')),
                              *((_reader_reserve(budget, 96) or (a['task_id'], 'coding_attempt')) for a in actual_attempts.values()))):
            _check(task_id not in storage.owners)
            _reader_put((_reader_reserve(budget, 96) or (kind, instance)), storage.owners, task_id, budget=budget)
    from runtime.workflows._coding_cancellation_history import complete
    complete(conn, org, budget, storage)
    from runtime.workflows.coding_native_history import validate_native_journals
    validate_native_journals(conn, org, budget, storage)
    _check(used_attempts == _reader_set((r['id'] for r in attempts), budget=budget) and used_events == _reader_set((r['id'] for r in events), budget=budget))
    return storage
