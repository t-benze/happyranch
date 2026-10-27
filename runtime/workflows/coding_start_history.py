"""SELECT-only complete queued K3a closure under the enclosing native budget.

The initial start is reconstructed independently, followed by the closed
ordered edges and native reviewer/QA readers. Unsupported later-stage/control
families refuse.
Saved validity never supplies current access, readiness or permission.
"""
from __future__ import annotations

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
               else tuple(row[1] for row in conn.execute(f'PRAGMA table_xinfo("{table}")')))
    allowed = (_TASK_TYPES if table == 'tasks' else _RESULT_TYPES if table == 'task_results'
               else (('text', 'blob', 'integer', 'real', 'null'),) * len(columns))
    for row in _bounded_rows(conn, table, columns, allowed, budget,
                             where=where, args=args, maximum=maximum,
                             value_limit=16777216 if table == 'workflow_coding_events' else 1048576):
        yield dict(zip(columns, row[:len(columns)], strict=True))


def same(left: Any, right: Any) -> bool:
    if type(left) is float and type(right) is float:
        return struct.pack('>d', left) == struct.pack('>d', right)
    return type(left) is type(right) and (
        left.keys() == right.keys() and all(same(left[k], right[k]) for k in left)
        if type(left) is dict else
        len(left) == len(right) and all(same(a, b) for a, b in zip(left, right, strict=True))
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

    def add(self, table: str, row: dict) -> None:
        key = _canonical(encode_row(row))
        self.admitted.setdefault(table, set()).add(key)

    def matches(self, table: str, row: dict) -> bool:
        return _canonical(encode_row(row)) in self.admitted.get(table, ())


def _reserved_rows(conn, table, column, budget, *, where, args=()):
    from runtime.infrastructure.workflow_coding_schema import _coding_metadata_candidate
    for rowid, raw in conn.execute(f'SELECT rowid,{column} FROM {table} WHERE ' + where
                                   + ' ORDER BY rowid', args):
        if _coding_metadata_candidate(raw, location=f'{table}.{column}'):
            yield from rows(conn, table, budget, where='rowid=?', args=(rowid,), maximum=1)


def read_queued(conn: sqlite3.Connection, org: str, budget: _Budget, mappings: Any, imported: Any) -> QueuedStorage:
    from runtime.infrastructure.workflow_schema import _coding_document_exclusions
    optional = _coding_document_exclusions(conn)
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
            envelope = json.loads(raw)
        except (ValueError, TypeError):
            continue  # Central lexical discriminator still refuses malformed coding.
        if type(envelope) is not dict or envelope.get('action') != 'workflow_start':
            continue
        _decoded(envelope, budget)
        decision = parse_native_decision(raw.encode('utf8'))
        if decision['workflow']['format'] == 'workflow-coding-start@2':
            _resolve_start(decision['workflow'], mappings, imported, budget)
        _integer(row['id'], 1)
        _check(row['status'] == 'completed' and row['waiting_on_job_ids'] is None)
        issuer = dict(task_id=row['task_id'], session_id=row['session_id'], principal=row['agent'])
        _session_start(conn, issuer, budget)
        native = _task(conn, row['task_id'], budget)
        _check(native._text('task_type') == 'task')
        storage.callbacks[row['id']] = (row, decision)
        storage.add('task_results', row)
    if storage.callbacks:
        from runtime.orchestrator.orchestrator import completion_report_from_result_row
        task_ids = tuple(sorted({r['task_id'] for r, _ in storage.callbacks.values()}))
        selection = "action='completion_report' AND task_id IN (" + ','.join('?' for _ in task_ids) + ')'
        for audit in _reserved_rows(conn, 'audit_log', 'payload', budget, where=selection, args=task_ids):
            if audit['payload'] is None:
                continue
            try:
                body = json.loads(audit['payload'])
            except ValueError:
                continue
            if type(body) is not dict or type(body.get('decision')) is not dict or body['decision'].get('action') != 'workflow_start':
                continue
            _decoded(body, budget)
            candidates = []
            for callback, _ in storage.callbacks.values():
                budget.step()
                expected = completion_report_from_result_row(callback['task_id'], callback).model_dump()
                if '_result_row_id' in body or '_result_session_id' in body:
                    expected.update(_result_row_id=callback['id'], _result_session_id=callback['session_id'])
                if (audit['task_id'], audit['agent']) == (callback['task_id'], callback['agent']) and same(expected, body):
                    candidates.append(callback)
            _check(len(candidates) == 1)
            storage.add('audit_log', audit)
    run_rows = list(rows(conn, 'workflow_coding_runs', budget))
    attempts = list(rows(conn, 'workflow_coding_attempts', budget))
    events = list(rows(conn, 'workflow_coding_events', budget))
    _check(len(run_rows) <= len(attempts) <= 10 * len(run_rows) and len(events) >= len(run_rows))
    used_attempts, used_events, used_sources = set(), set(), set()
    for run in run_rows:
        budget.step()
        _check(imported is not None and mappings is not None)
        instance = run['instance_id']
        selected = [r for r in events if r['instance_id'] == instance]
        selected_attempts = [r for r in attempts if r['instance_id'] == instance]
        selected.sort(key=lambda row: row['event_seq'])
        _check(len(selected) >= 1 and 1 <= len(selected_attempts) <= 10)
        event = selected[0]
        actual_run = run
        initial = _read_json(event['event_bytes'], budget, 16777216)
        run = initial['after']['run']
        attempt = decode_row(initial['after']['attempts'][0]['record'])
        _check(event['id'] not in used_events and attempt['id'] not in used_attempts)
        for selected_event in selected:
            _check(selected_event['id'] not in used_events)
            used_events.add(selected_event['id'])
        _check(type(event['event_bytes']) is bytes and _hash(event['event_bytes']) == event['event_digest'])
        payload = _closed(_read_json(event['event_bytes'], budget, 16777216),
            'format id instance_id event_seq event_kind attempt_id operation previous_digest before after evidence created_at')
        _check(payload['format'] == 'workflow-coding-event@1'
               and payload['event_kind'] == 'run_started' and payload['event_seq'] == 1
               and payload['previous_digest'] is None)
        _check(_canonical(payload) == event['event_bytes'])
        for key in ('id', 'instance_id', 'event_seq', 'event_kind', 'attempt_id', 'previous_digest', 'created_at'):
            _check(same(payload[key], event[key]))
        _timestamp(payload['created_at'])
        evidence = _closed(payload['evidence'], 'kind command origin authority rows facts native_task_observations')
        _check(evidence['kind'] == 'run_started' and evidence['native_task_observations'] == [])
        decision = parse_decision(_canonical(dict(action='workflow_start', workflow=evidence['command'])))
        command = decision['workflow']
        digest = _hash(_canonical(command))
        operation = dict(org_slug=org, principal=event['principal'], key=command['operation_key'], request_digest=digest)
        _check(payload['operation'] == operation and event['org_slug'] == org
               and event['operation_key'] == operation['key'] and event['request_digest'] == digest
               and event['id'] == identity('event', org, instance, event['principal'], operation['key']))
        facts = _closed(evidence['facts'], 'source carrier_task_id release mapping_id mapping_digest context_id '
                         'binding_snapshot_id authorization_id activation_id first_attempt_id')
        source = _closed(facts['source'], 'task_id session_id principal result result_claim parent_task_id root_ancestor_id budgets')
        result = _closed(source['result'], 'id record')
        _integer(result['id'], 1)
        _check(result['id'] in storage.callbacks and source['task_id'] not in used_sources)
        used_sources.add(source['task_id'])
        callback, stored_decision = storage.callbacks[result['id']]
        _check(same(result['record'], encode_row(callback)) and stored_decision == decision
               and (callback['task_id'], callback['agent'], callback['session_id']) ==
                   (source['task_id'], source['principal'], source['session_id']))
        _check(event['principal'] == 'agent:' + source['principal']
               and instance == identity('root', org, source['task_id'], result['id'], digest))
        origin = _closed(evidence['origin'], 'kind principal task_id session_id result_id claim')
        _check(origin == dict(kind='manager_result', principal=source['principal'], task_id=source['task_id'],
                             session_id=source['session_id'], result_id=result['id'], claim=None))
        claim = _closed(source['result_claim'], 'native_claim owner_invocation_id action_fingerprint')
        _token(claim['owner_invocation_id'])
        _check(claim['action_fingerprint'] == _hash(callback['decision_json'].encode('utf8')))
        before = _closed(payload['before'], 'run attempts instances tasks rounds requests assignments')
        after = _closed(payload['after'], 'run attempts instances tasks rounds requests assignments')
        _check(before['run'] is None and same(after['run'], run))
        for name in ('rounds', 'requests', 'assignments'):
            _check(before[name] == after[name] == [])
        _check(type(after['tasks']) is list and len(after['tasks']) == len(before['tasks']) == 3)
        after_tasks = {r['id']: decode_row(r['record']) for r in after['tasks']}
        before_tasks = {r['id']: None if r['record'] is None else decode_row(r['record']) for r in before['tasks']}
        _check(len(after_tasks) == 3 and after_tasks.keys() == before_tasks.keys())
        source_before = before_tasks[source['task_id']]
        _check(type(source_before) is dict and tuple(source_before) != ()
               and set(source_before) == set(_TASK_COLUMNS)
               and same(claim['native_claim'], claim_for(source_before, result['id'])))
        _check(source_before['status'] == 'in_progress' and source_before['task_type'] == 'task'
               and source_before['assigned_agent'] == source['principal']
               and source_before['current_session_id'] == source['session_id'])
        _check(source_before['completed_at'] is None and source_before['zombie_flagged_at'] is None)
        for key in ('block_kind', 'active_chain', 'active_fanout', 'blocked_on_job_ids', 'cancelled_at'):
            _check(source_before[key] is None)
        expected_source = {**source_before, 'block_kind': 'delegated', 'note': 'coding-owned:' + instance,
                           'updated_at': payload['created_at']}
        _check(same(after_tasks[source['task_id']], expected_source))
        pin_entries = [e for e in mappings.mappings if e.mapping_id == command['mapping']['id']]
        _check(len(pin_entries) == 1)
        mapping_entry = pin_entries[0]
        _check(command['mapping'] == dict(id=mapping_entry.mapping_id, generation=mapping_entry.generation,
                                         digest=mapping_entry.mapping_digest) and run['mapping_id'] == mapping_entry.mapping_id)
        record = _read_json(mapping_entry.mapping_bytes, budget, 1048576)
        settings = _resolve_start(command, mappings, imported, budget)
        mapping = record['mapping']
        effective = effective_mapping(mapping, command)
        roles = effective['roles']
        _roles(roles, {k: [] for k in roles})
        _check(roles['agent_manager'] == dict(principal=source['principal'], team=source_before['team']))
        grant, snapshot = _read_authority(evidence['authority'], org, budget)
        _check(grant == _canonical(command))
        _configuration(mapping, snapshot, command, budget)
        _configuration(effective, snapshot, command, budget)
        carrier_id = facts['carrier_task_id']
        _check(carrier_id in after_tasks and carrier_id != source['task_id'])
        carrier, leaf = after_tasks[carrier_id], after_tasks[attempt['task_id']]
        _check(carrier['parent_task_id'] == source['task_id'] and leaf['parent_task_id'] == carrier_id)
        for row, binding, state, block, brief in (
            (carrier, roles['agent_manager'], 'in_progress', 'delegated', 'Structural coding carrier ' + instance),
            (leaf, roles['implementer'], 'pending', None, task_brief(command['work'])),
        ):
            _check(set(row) == set(_TASK_COLUMNS) and row['task_type'] == 'subtask'
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
        context_rows = [r for r in evidence['rows'] if r.get('table') == 'workflow_contexts']
        _check(len(context_rows) == 1)
        frozen_context = decode_row(context_rows[0]['record'])
        context_value = _read_json(frozen_context['context_bytes'], budget, 1048576)
        native_source = _closed(context_value['native_source'], 'ancestors session_start invocation checkpoints')
        checkpoints = native_source['checkpoints']
        _check(type(checkpoints) is list and 1 <= len(checkpoints) <= 5)
        from runtime.workflows.coding_source_history import _timestamp as native_timestamp
        baseline = None
        for observed in checkpoints:
            _closed(observed, 'source ancestors')
            _check(type(observed['ancestors']) is list and len(observed['ancestors']) < 20)
            records = [decode_row(observed['source']), *[decode_row(r) for r in observed['ancestors']]]
            for record in records:
                _check(set(record) == set(_TASK_COLUMNS))
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
                            _check(same(old[name], new[name]))
            baseline = records
        _check(same(baseline[0], source_before)
               and same(checkpoints[-1]['ancestors'], native_source['ancestors']))
        chain = [source_before]
        seen = {source['task_id']}
        _check(type(native_source['ancestors']) is list and len(native_source['ancestors']) < 20)
        for encoded in native_source['ancestors']:
            parent = decode_row(encoded)
            _check(set(parent) == set(_TASK_COLUMNS) and chain[-1]['parent_task_id'] == parent['id']
                   and parent['id'] not in seen and parent['team'] == source_before['team']
                   and parent['cancelled_at'] is None
                   and parent['status'] not in ('failed', 'completed', 'cancelled', 'superseded'))
            seen.add(parent['id'])
            chain.append(parent)
        _check(chain[-1]['parent_task_id'] is None and source['root_ancestor_id'] == chain[-1]['id']
               and source['parent_task_id'] == source_before['parent_task_id'])
        # Current ancestry may have native observations, but no owner/edge drift.
        current_ancestors = []
        for historical in chain[1:]:
            current = _task(conn, historical['id'], budget)
            actual = {c.name: c.value.decode('utf8') if c.storage_class == 'text' else c.value for c in current.columns}
            current_ancestors.append((actual, historical))
        start = _session_start(conn, dict(task_id=source['task_id'], session_id=source['session_id'], principal=source['principal']), budget)
        actual_start = {c.name: c.value.decode('utf8') if c.storage_class == 'text' else c.value for c in start.columns}
        _check(same(native_source['session_start'], actual_start))
        invocation = _closed(native_source['invocation'], 'id owner entry')
        _check(invocation['id'] == claim['owner_invocation_id'] and invocation['owner'] in ('ordinary', 'v2'))
        from runtime.infrastructure.task_pause_controls import decode_journal
        entry = invocation['entry']
        decode_journal(_canonical(dict(version=1, entries=[entry])).decode('utf8'))
        # Validate original admission against the frozen source, independently
        # of reconstructed pins or today's mutable native invocation lifecycle.
        _check(not entry['context'].get('execution_unknown'))
        _check(entry['fingerprint']['brief_digest'] == _hash(source_before['brief'].encode('utf8')))
        _check(entry['id'] == invocation['id'] and entry['owner'] == invocation['owner']
               and entry['task_id'] == source['task_id'] and entry['session_id'] == source['session_id']
               and entry['agent'] == source['principal'] and entry['phase'] == 'running'
               and entry['root_task_id'] == source['root_ancestor_id'] and entry['org'] == org
               and entry['fingerprint']['count'] + 1 == source_before['orchestration_step_count'])
        limits = _closed(source['budgets'], 'source_step source_revision session_timeout_seconds max_revise_rounds '
                         'coding_attempt_limit coding_event_limit coding_revision_limit coding_same_principal_retry_limit')
        for key, value in limits.items():
            if value is not None:
                _integer(value)
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
            template=template, carrier_id=carrier_id, instance_id=instance, created_at=payload['created_at'])
        _check(same(evidence['rows'], journal_rows(expected_pins)))
        _capture_admission(conn, expected_pins['workflow_admission_records'][0], evidence['authority'], budget)
        _check(len(selected_attempts) <= limits['coding_attempt_limit'])
        scope = _canonical(initial_scope(org=org, instance=instance, attempt=attempt['id'], command=command,
                                        roles=roles, context=context))
        _check(same(attempt, dict(id=identity('attempt', instance, 1), instance_id=instance,
            revision=1, stage='development', attempt_sequence=1, assignment_generation=1,
            predecessor_id=None, task_id=leaf['id'], principal=roles['implementer']['principal'], session_id=None,
            result_id=None, submission_id=None, review_request_id=None, state='queued',
            scope_bytes=scope, scope_digest=_hash(scope))))
        _check(same(run, dict(instance_id=instance, mapping_id=mapping_entry.mapping_id, cas_version=1,
            current_revision=0, state='development', current_attempt_id=attempt['id'], last_event_id=event['id'])))
        _check(facts == dict(source=source, carrier_task_id=carrier_id, release=command['release'],
            mapping_id=mapping_entry.mapping_id, mapping_digest=mapping_entry.mapping_digest,
            context_id=identity('context', instance), binding_snapshot_id=identity('binding', instance),
            authorization_id=identity('authorization', instance), activation_id=identity('activation', instance),
            first_attempt_id=attempt['id']))
        for name, values in (('attempts', [attempt]), ('instances', expected_pins['workflow_instances'])):
            key = 'id'
            _check(before[name] == [dict(id=r[key], record=None) for r in values]
                   and after[name] == [dict(id=r[key], record=encode_row(r)) for r in values])
        for table, values in expected_pins.items():
            for expected in values:
                key = 'id' if 'id' in expected else 'namespace' if 'namespace' in expected else 'instance_id' if 'instance_id' in expected else 'activation_id'
                actual = list(rows(conn, table, budget, where=f'{key}=?', args=(expected[key],), maximum=2))
                _check(len(actual) == 1 and same(actual[0], expected))
                storage.add(table, expected)
        # Reverse references are part of ownership too: a second legacy
        # instance/context/binding/activation cannot alias these new pins just
        # because its own scalar columns contain no coding discriminator.
        for table, column, key in (
            ('workflow_active_authorizations', 'authorization_revision_id', identity('authorization', instance)),
            ('workflow_binding_snapshots', 'authorization_revision_id', identity('authorization', instance)),
            ('workflow_contexts', 'binding_snapshot_id', identity('binding', instance)),
            ('workflow_instances', 'binding_snapshot_id', identity('binding', instance)),
            ('workflow_instances', 'context_id', identity('context', instance)),
            ('workflow_activations', 'instance_id', instance),
            ('workflow_active_activations', 'activation_id', identity('activation', instance)),
            ('workflow_activation_operations', 'activation_id', identity('activation', instance)),
        ):
            actual = list(rows(conn, table, budget, where=f'{column}=?', args=(key,), maximum=2))
            _check(same(actual, expected_pins[table]))
        from runtime.workflows.coding_native_history import read_native
        returned = [e for e in selected[1:] if e['event_kind'] == 'return']
        _check(len(returned) <= 1)
        storage.return_boundary = None if not returned else returned[0]['created_at']
        comparison_start = len(storage.cancellation_comparisons)
        native = read_native(conn, org, budget, storage, start=event, events=selected[1:],
            tasks=after_tasks, source=source, roles=roles, authority=evidence['authority'],
            command_value=command, mapping=mapping, source_invocation=entry,
            source_ancestors=[decode_row(r) for r in native_source['ancestors']])
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
                actual = list(rows(conn, 'tasks', budget, where='id=?', args=(historical['id'],), maximum=1))
                _check(len(actual) == 1)
                current = actual[0]
                for key in ('id', 'task_type', 'parent_task_id', 'revisit_of_task_id', 'team',
                            'assigned_agent', 'brief', 'created_at', 'dispatched_from_thread_id'):
                    _check(same(current[key], historical[key]))
                _check(datetime.fromisoformat(current['updated_at']) >=
                       datetime.fromisoformat(storage.return_boundary.replace('Z', '+00:00')))
            del storage.cancellation_comparisons[comparison_start:]
        storage.return_boundary = None
        actual_attempts = {a['id']: a for a in selected_attempts}
        _check(len(actual_attempts) == len(selected_attempts)
               and same(native['run'], actual_run) and same(native['attempts'], actual_attempts))
        _check(not (set(actual_attempts) & used_attempts))
        used_attempts.update(actual_attempts)
        actual_attempt = actual_attempts[actual_run['current_attempt_id']]
        after_tasks = native['tasks']
        run, attempt = actual_run, actual_attempt
        for task_id, expected in after_tasks.items():
            actual = list(rows(conn, 'tasks', budget, where='id=?', args=(task_id,), maximum=1))
            _check(len(actual) == 1 and same(actual[0], expected))
            storage.add('tasks', expected)
            roots = list(rows(conn, 'workflow_instances', budget, where='root_task_id=?', args=(task_id,), maximum=2))
            _check(same(roots, expected_pins['workflow_instances'] if task_id == carrier_id else []))
        # No alternate dispatcher, result, submission, assignment or premature
        # downstream stage may share this closed queued graph.
        for table, column, keys in (
            ('workflow_draft_dispatch_intents', 'task_id', after_tasks),
            ('workflow_request_task_bridges', 'task_id', after_tasks),
            ('workflow_instance_tasks', 'instance_id', [instance]),
            ('workflow_task_results', 'instance_id', [instance]),
            ('workflow_current_assignments', 'instance_id', [instance]),
            ('workflow_instance_contributors', 'instance_id', [instance]),
            ('workflow_dispatch_operations', 'instance_id', [instance]),
            ('workflow_operation_replays', 'instance_id', [instance]),
            ('workflow_author_handoffs', 'instance_id', [instance]),
            ('workflow_document_revision_operations', 'instance_id', [instance]),
            ('workflow_dispatch_callbacks', 'task_id', after_tasks),
            ('workflow_submissions', 'instance_id', [instance]),
            ('workflow_rounds', 'instance_id', [instance]),
            ('workflow_events', 'instance_id', [instance]),
            ('task_results', 'task_id', [carrier_id]),
        ):
            if table in ('workflow_draft_dispatch_intents', 'workflow_author_handoffs',
                         'workflow_document_revision_operations') and table not in optional:
                continue
            for key in keys:
                budget.step()
                _check(conn.execute(f'SELECT 1 FROM {table} WHERE {column}=? LIMIT 1', (key,)).fetchone() is None)
        for owned_attempt in actual_attempts.values():
            owned_state = native['stages'][owned_attempt['id']]
            callbacks = list(rows(conn, 'task_results', budget, where='task_id=?', args=(owned_attempt['task_id'],)))
            _check(same(callbacks, [] if owned_state['callback'] is None else [owned_state['callback']]))
            budget.step()
            _check(conn.execute('SELECT 1 FROM tasks WHERE parent_task_id=? LIMIT 1', (owned_attempt['task_id'],)).fetchone() is None)
            storage.add('workflow_coding_attempts', owned_attempt)
        for child in rows(conn, 'tasks', budget, where='parent_task_id=?', args=(source['task_id'],)):
            _check(child['id'] == carrier_id or (child['status'] in ('completed', 'failed', 'cancelled', 'superseded')
                and datetime.fromisoformat(child['created_at']) <= datetime.fromisoformat(payload['created_at'].replace('Z', '+00:00'))))
        _check({r['id'] for r in rows(conn, 'tasks', budget, where='parent_task_id=?',
                                    args=(carrier_id,), maximum=10)} == {a['task_id'] for a in actual_attempts.values()})
        for table, row in (('workflow_coding_runs', run), ('workflow_coding_attempts', attempt), ('workflow_coding_events', event)):
            storage.add(table, row)
        storage.runs[instance] = dict(run=run, command=command, roles=roles, attempt=attempt, event=event,
                                    source=source, attempts=actual_attempts, mapping=mapping, binding=expected_pins['workflow_binding_snapshots'][0],
                                    native=native, authority=evidence['authority'], tasks=after_tasks, settings=settings)
        for task_id, kind in ((source['task_id'], 'coding_source_waiting'),
                              (carrier_id, 'coding_carrier'),
                              *((a['task_id'], 'coding_attempt') for a in actual_attempts.values())):
            _check(task_id not in storage.owners)
            storage.owners[task_id] = (kind, instance)
    from runtime.workflows._coding_cancellation_history import complete
    complete(conn, org, budget, storage)
    from runtime.workflows.coding_native_history import validate_native_journals
    validate_native_journals(conn, org, budget, storage)
    _check(used_attempts == {r['id'] for r in attempts} and used_events == {r['id'] for r in events})
    return storage
